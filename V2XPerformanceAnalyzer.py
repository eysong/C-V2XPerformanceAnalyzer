from lxml import etree
import time
import pandas as pd
import seaborn as sns
import sys
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages
from pdf2docx import Converter
import folium
from geopy.distance import geodesic
import argparse

def main():
    # Parse all given arguments
    parser = argparse.ArgumentParser(
        description="C-V2X performance analyzer — matches Tx/Rx PDML captures and reports metrics.")
    parser.add_argument("tx_file", help="Transmitted PDML file")
    parser.add_argument("rx_file", help="Received PDML file")
    parser.add_argument("address", nargs="?", default=None,
                        help="Tx MAC or IPv6 address — only for combined Tx files")
    parser.add_argument("--rx-location", default=None,
                        help="Receiver coordinates as LAT,LON (enables map + distance graph)")
    args = parser.parse_args()

    # Parse location data if given
    rxLat = rxLng = None
    if args.rx_location:
        try:
            rxLat, rxLng = map(float, args.rx_location.split(","))
        except ValueError:
            print("Error: --rx-location must be two numbers as LAT,LON (e.g. 39.13,-77.21)")
            sys.exit(1)
    spatialEnabled = rxLat is not None

    # Load the Tx packets, filtering by address if one was given
    if args.address:
        print("Parsing Tx File...")
        txList, _ = separateByAddress(etree.parse(args.tx_file).getroot(), args.address)
        print("Done!")
    else:
        print("Parsing Tx File...")
        txList = list(etree.parse(args.tx_file).getroot())
        print("Done!")

    print("Parsing Rx File...")
    rxTree = etree.parse(args.rx_file)
    print("Done!")
    print("Building Rx map...")
    rxMap = buildRxMap(rxTree.getroot())
    print("Done!")
    packetMatches = []
    packetLosses = []

    # Get initial characteristics from each packet
    initialReqs = {'frame.len', 'frame.time_delta_displayed', 'frame.time_epoch', 'frame.number', 'j2735.messageId', 'j2735.long_01', 'j2735.lat_03'}

    # Iteration of Tx packets
    for packet in txList:

        # Obtain necessary starting fields from Tx packet
        txFields = get_field_values(packet, initialReqs)
        txType = txFields.get('j2735.messageId')
        if txType is None:
            continue
        matchResult = None

        # Compare txType with each currently supported message ID
        if txType == '18':
            matchResult = matchMAP(packet, rxMap)
        elif txType == '19':
            matchResult = matchSPAT(packet, rxMap)
        elif txType == '20':
            matchResult = matchBSM(packet, rxMap)
        elif txType == '31':
            matchResult = matchTIM(packet, rxMap)

        # If there is no packet match found, the Tx packet is considered dropped
        if matchResult is None:
            packetLosses.append(packet)

        # Match found, add the match to a list
        else:
            matchedPacket, latency = matchResult
            packetMatches.append((packet, matchedPacket, latency, txType))

    # Data structures for metric calculation
    rxTimestamps = {'18': [], '19': [], '20': [], '31': []}
    packetTimeline = []
    packetLengths = []
    mapPoints = []

    # Fill lists with necessary data for metric calculations
    for (packet, matchingPacket, latency, txType) in packetMatches:
        packetFields = get_field_values(packet, initialReqs)
        matchFields = get_field_values(matchingPacket, initialReqs)

        packetTimeline.append((float(packetFields['frame.time_epoch']), True))
        rxTimestamps[txType].append(float(matchFields['frame.time_epoch']))
        packetLengths.append(((float(matchFields['frame.time_epoch'])), (int(matchFields['frame.len']))))

        # If making a map, create a list of points for the map
        if spatialEnabled:
            lat = packetFields.get('j2735.lat_03')
            long = packetFields.get('j2735.long_01')
            if lat is not None and long is not None:
                lat = float(lat) / 1e7
                long = float(long) / 1e7
                mapPoints.append((float(packetFields['frame.time_epoch']), lat, long, True))

    # Packet loss test and more data for PER
    for packet in packetLosses:
        packetFields = get_field_values(packet, initialReqs)
        packetTimeline.append((float(packetFields['frame.time_epoch']), False))

        # If making a map, create a list of points for the map
        if spatialEnabled:
            lat = packetFields.get('j2735.lat_03')
            long = packetFields.get('j2735.long_01')
            if lat is not None and long is not None:
                lat = float(lat) / 1e7
                long = float(long) / 1e7
                mapPoints.append((float(packetFields['frame.time_epoch']), lat, long, False))

    # Sort the tx packets (along with their match booleans) by timestamp
    packetTimeline = sorted(packetTimeline)

    # Trim off the packets after the last received one from the timeline
    packetTimeline, trimmedCount = trimTrailingLosses(packetTimeline)

    if trimmedCount > 0:
        note = (f"NOTE: {trimmedCount} trailing lost packet(s) removed from analysis "
                f"(receiver believed powered off at capture end).")
        print(note)

    # Trim packets from map if necessary
    if spatialEnabled and packetTimeline:
        cutoffTime = packetTimeline[-1][0]   # last timestamp that survived the trim
        mapPoints = [p for p in mapPoints if p[0] <= cutoffTime]

    # Perform final metric calculation and analysis
    perFig, perValues = calculatePER(packetTimeline)
    latFig, latenciesByType, negativeFlag = calculateLatency(packetMatches)
    ipgFig, outlierWarnIPG = calculateIPG(rxTimestamps)
    throughputs = calculateThroughput(packetLengths)

    figs = [perFig, latFig, ipgFig]

    # Spatial outputs only when coordinates were provided
    if spatialEnabled:
        mapPoints = sorted(mapPoints, key=lambda p: p[0])
        plotMap(mapPoints, rxLat, rxLng)
        distanceFig = plotDistance(mapPoints, rxLat, rxLng)
        if distanceFig is not None:
            figs.insert(1, distanceFig)

    statsText = buildStatsText(packetMatches, packetLosses, latenciesByType, negativeFlag, outlierWarnIPG,
                               perValues, rxTimestamps, throughputs)

    saveReport(figs, statsText)

def separateByAddress(tree, txMac):
    matchesAddress = []
    notMatchesAddress = []
    
    macFields = {'ipv6.src', 'wlan.sa'}
    
    for packet in tree:
        # Attempts to get a MAC or IP address for each packet
        fields = get_field_values(packet, macFields)
        srcMac = fields.get('ipv6.src') or fields.get('wlan.sa')
        
        if srcMac is None:
            continue
        # Address match check
        if srcMac.lower() == txMac.lower():
            matchesAddress.append(packet)
        else:
            notMatchesAddress.append(packet)
    
    return matchesAddress, notMatchesAddress

def buildRxMap(rxTree):
    # Build a map of keys that store received packets with matching characteristics
    #   based on their message type
    rxMap = {'18': {}, '19': {}, '20': {}, '31': {}}

    relevantFields = {'j2735.msgCnt', 'j2735.lat_03', 'j2735.long_01', 'frame.time_epoch', 
                      'j2735.messageId', 'j2735.secMark', 'j2735.id', 
                      'j2735.signalGroup', 'j2735.minEndTime_01', 'j2735.id_01'}
    
    for rxPacket in rxTree:
        fields = get_field_values(rxPacket, relevantFields)
        msgId = fields.get('j2735.messageId')
        if msgId not in rxMap:
            continue
        
        # MAP: Intersection Id, Latitude and Longitude
        if msgId == '18':
            key = (make_key(fields.get('j2735.id_01')),
                   make_key(fields.get('j2735.lat_03')),
                   make_key(fields.get('j2735.long_01')))
        # SPaT: Intersection Id, SignalGroups and minEndTimes
        elif msgId == '19':
            groups = list_check(fields.get('j2735.signalGroup'))
            times  = list_check(fields.get('j2735.minEndTime_01'))
            key = (make_key(fields.get('j2735.id_01')), frozenset(zip(groups, times)))
        # BSM: Vehicle Id, messageCount and secondMark
        elif msgId == '20':
            key = (make_key(fields.get('j2735.id')),
                   make_key(fields.get('j2735.msgCnt')),
                   make_key(fields.get('j2735.secMark')))
        # TIM: messageCount, Latitude and Longitude
        elif msgId == '31':
            key = (make_key(fields.get('j2735.msgCnt')),
                   make_key(fields.get('j2735.lat_03')),
                   make_key(fields.get('j2735.long_01')))

        rxMap[msgId].setdefault(key, []).append(rxPacket)

    return rxMap

# Helpers for dictionary storage of matching packet keys        
def make_key(val):
    if isinstance(val, list):
        return tuple(val)
    return val  

def list_check(val):
    if val is None:
        return []
    elif isinstance(val, list):
        return val
    return [val]


# Finds all fields given (requiredFields) by traversing the PDML
def get_field_values(packet, requiredFields):
    result = {}
    for field in packet.iter('field'):
        fieldName = field.get('name')
        if fieldName in requiredFields:
            if fieldName in result:
                if not isinstance(result[fieldName], list):
                    result[fieldName] = [result[fieldName]]
                result[fieldName].append(field.get('show'))
            else:
                result[fieldName] = field.get('show')
    return result
    

def matchTIM(txPacket, rxMap, cutoffTime=0.5):
    TIMrequiredFields = {'j2735.msgCnt', 'j2735.lat_03', 'j2735.long_01', 'frame.time_epoch'}

    # Create Tx packet's key
    txFields = get_field_values(txPacket, TIMrequiredFields)
    key = (make_key(txFields.get('j2735.msgCnt')), make_key(txFields.get('j2735.lat_03')), 
            make_key(txFields.get('j2735.long_01')))

    # Look for matches and, if any exist, find the "best match"
    matches = rxMap['31'].get(key, [])
    return findBestMatch(txPacket, matches, cutoffTime)

def matchBSM(txPacket, rxMap, cutoffTime=1):
    BSMrequiredFields = {'j2735.msgCnt', 'j2735.secMark', 'j2735.id', 'frame.time_epoch'}

    # Create Tx packet's key
    txFields = get_field_values(txPacket, BSMrequiredFields)
    key = (make_key(txFields.get('j2735.id')), 
           make_key(txFields.get('j2735.msgCnt')), 
           make_key(txFields.get('j2735.secMark')))

    # Look for matches and, if any exist, find the "best match"
    matches = rxMap['20'].get(key, [])
    return findBestMatch(txPacket, matches, cutoffTime)

def matchSPAT(txPacket, rxMap, cutoffTime=1):
    SPATrequiredFields = {'j2735.id_01', 'j2735.signalGroup', 'j2735.minEndTime_01', 'frame.time_epoch'}

    # Create Tx packet's key
    txFields = get_field_values(txPacket, SPATrequiredFields)
    groups = list_check(txFields.get('j2735.signalGroup'))
    times  = list_check(txFields.get('j2735.minEndTime_01'))
    key = (make_key(txFields.get('j2735.id_01')), frozenset(zip(groups, times)))

    
    # Look for matches and, if any exist, find the "best match"
    matches = rxMap['19'].get(key, [])
    return findBestMatch(txPacket, matches, cutoffTime)  

def matchMAP(txPacket, rxMap, cutoffTime=0.5):
    MAPrequiredFields = {'j2735.id_01', 'j2735.lat_03', 'j2735.long_01', 'frame.time_epoch'}

    # Create Tx packet's key
    txFields = get_field_values(txPacket, MAPrequiredFields)
    key = (make_key(txFields.get('j2735.id_01')), make_key(txFields.get('j2735.lat_03')), 
           make_key(txFields.get('j2735.long_01')))

    
    # Look for matches and, if any exist, find the "best match"
    matches = rxMap['18'].get(key, [])
    return findBestMatch(txPacket, matches, cutoffTime)
    

# Using packet timestamps, find the best possible match for the given Tx packet if multiple matches exist
def findBestMatch(txPacket, matches, cutoffTime):
    if not matches:
        return None

    # Get transmit packet's timestamp
    txTime = float(txPacket.find(".//field[@name='frame.time_epoch']").get('show'))

    # Anything higher than cutoffTime is ignored
    minAbsDiff = cutoffTime
    bestMatch = None
    bestDiff = None

    for packet in matches:
        rxTime = float(packet.find(".//field[@name='frame.time_epoch']").get('show'))
        diff = rxTime - txTime

        # If true, a better match has been found
        if abs(diff) < minAbsDiff:
            minAbsDiff = abs(diff)
            bestMatch = packet
            bestDiff = diff 

    if bestMatch is not None:
        return (bestMatch, bestDiff)
    return None

def calculatePER(txPackets, windowSize=50):
    if(len(txPackets) == 0):
        print("No packets found, aborting PER calculation!")
        return None
    windows = []
    initialTime = txPackets[0][0]
    
    # For each window, find the time of first packet in window and the number of packets lost
    for i in range(len(txPackets)-windowSize + 1):
        window = txPackets[i : i + windowSize]
        windowTime = window[-1][0] - initialTime
        losses = sum(1 for _, received in window if received == False)
        per = losses / windowSize * 100
        windows.append((windowTime, per))
        # print(f"  t={windowTime:.3f}  PER={per:.1f}%  ({losses}/{windowSize} lost)")
    
    # Pandas dataframe packing
    timeList, perList = map(list, zip(*windows))
    per_df = pd.DataFrame({'time': timeList, 'PER': perList})
    fig, ax = plt.subplots(figsize=(12, 5))

    # Seaborn graphing
    sns.lineplot(data=per_df, x='time', y='PER', ax=ax,
                 linewidth=1, label='Raw PER')


    ax.set_xlabel('Time (s)')
    ax.set_ylabel('PER (%)')
    ax.set_title(f'Packet Error Rate (window={windowSize} packets)')
    ax.set_ylim(0, 100)
    ax.legend()
    plt.tight_layout()
    ax.set_rasterized(True)
    return fig, perList
    

def calculateLatency(packetMatches):
    if(len(packetMatches) == 0):
        print("No packets found, aborting Latency calculation!")
        return None
    TYPE_NAMES = {'18': 'MAP', '19': 'SPAT', '20': 'BSM', '31': 'TIM'}
    latenciesByType = {'18': [], '19': [], '20': [], '31': []}
    latency_data = []
    negativeFlag = False

    # Search for negative latency values, then scale to ms and sort by message type
    for (txPacket, _, latency, txType) in packetMatches:
        if latency < 0:
            negativeFlag = True
        latenciesByType[txType].append(latency * 1000) 

    # For each message type, print data and create data for dataFrame
    for msgId, latencies in latenciesByType.items():
        if len(latencies) == 0:
            continue
        latencyNP = np.array(latencies)
        for latency in latencies:
            latency_data.append({'latency': latency, 'type': TYPE_NAMES.get(msgId)})
        print(f"\n--- Latency Stats for message type {msgId}: ({len(latencyNP)} matched packets) ---")
        print(f"  Mean   : {np.mean(latencyNP):.3f} ms")
        print(f"  Median : {np.median(latencyNP):.3f} ms")
        print(f"  P95    : {np.percentile(latencyNP, 95):.3f} ms")
        print(f"  P99    : {np.percentile(latencyNP, 99):.3f} ms")
        print(f"  Min    : {np.min(latencyNP):.3f} ms")
        print(f"  Max    : {np.max(latencyNP):.3f} ms")
        print(f"  Std Dev: {np.std(latencyNP):.3f} ms")

    # DataFrame for seaborn graph
    latency_df = pd.DataFrame(latency_data)
    fig, ax = plt.subplots(figsize=(8, 5))

    # Seaborn graph
    sns.ecdfplot(data=latency_df, x='latency', hue='type', ax=ax, linewidth=2)
    ax.set_title("Latency CDF by message type")
    ax.set_xlabel('Latency (ms)')
    ax.set_ylabel('Cumulative proportion')
    ax.set_rasterized(True)
    return fig, latenciesByType, negativeFlag

def calculateIPG(packetTimestamps):
    TYPE_NAMES = {'18': 'MAP', '19': 'SPAT', '20': 'BSM', '31': 'TIM'}
    ipg_data = []
    outlierWarnIPG = False

    # Sort IPG by message type
    for msgType, packets in packetTimestamps.items():
        if len(packets) < 2:
            continue
        else:
            timestamps = np.array(packets)
            timestamps.sort()
            gaps = np.diff(timestamps) * 1000

            # Filter out any gaps greater than 3 seconds
            filteredGaps = gaps[gaps < 3000]
            if len(filteredGaps) < len(gaps):
                print("Gap outliers found (> 3 seconds), removed from graph data")
                outlierWarnIPG = True
            for packetGap in filteredGaps:
                ipg_data.append({'IPG': packetGap, 'type': TYPE_NAMES.get(msgType)})

            print(f"\n--- IPG Stats for message type {msgType}: ---")
            print(f"  Mean   : {np.mean(gaps):.3f} ms")
            print(f"  Median : {np.median(gaps):.3f} ms")
            print(f"  P95    : {np.percentile(gaps, 95):.3f} ms")
            print(f"  P99    : {np.percentile(gaps, 99):.3f} ms")
            print(f"  Min    : {np.min(gaps):.3f} ms")
            print(f"  Max    : {np.max(gaps):.3f} ms")
            print(f"  Std Dev: {np.std(gaps):.3f} ms")
    # DataFrame packing
    ipg_df = pd.DataFrame(ipg_data)

    # Graph creation
    fig, ax = plt.subplots(figsize=(8, 5))
    sns.ecdfplot(data=ipg_df, x='IPG', hue='type', ax=ax, linewidth=2)
    ax.set_title("Inter-packet Gap (IPG) by message type")
    ax.set_xlabel('IPG (ms)')
    ax.set_ylabel('Cumulative proportion')
    ax.set_rasterized(True)
    return fig, outlierWarnIPG

def calculateThroughput(packetLengths, winSeconds=5):
    if not packetLengths:
        return "no matched packets"
    packetLengths.sort()
    tStart = packetLengths[0][0]
    tEnd = packetLengths[-1][0]

    winStart = tStart
    throughputs = []
    windowTimes = []

    # Find bytes received in each time window and convert to bits per second
    while winStart + winSeconds <= tEnd:
        winEnd = winStart + winSeconds
        bytes = sum(length for time, length in packetLengths if winStart <= time < winEnd)
        throughputs.append((bytes * 8) / winSeconds)
        windowTimes.append(winStart - tStart)
        winStart += winSeconds

    # Display statistics
    if throughputs:
        arr = np.array(throughputs)
        print("\n --- Throughput Statistics ---")
        print(f"  Mean : {np.mean(arr):.2f} bps")
        print(f"  Max  : {np.max(arr):.2f} bps")
        print(f"  Min  : {np.min(arr):.2f} bps")
    
    return throughputs

    # throughput_df = pd.DataFrame({
    #     'time': windowTimes,
    #     'throughput': throughputs
    # })

    # fig, ax = plt.subplots(figsize=(10, 5))

    # sns.lineplot(data=throughput_df, x='time', y='throughput', ax=ax, linewidth=2, drawstyle='steps-post')

    # ax.set_xlabel('Time (s)')
    # ax.set_ylabel('Throughput (bps)')
    # ax.set_title('Throughput over time (5s windows)')

    # plt.tight_layout()
    # plt.savefig('throughput.png', dpi=150)
    # plt.show()


def plotMap(points, rxLat, rxLng, outputPath='map_trail.html'):
    
    if not points:
        print("No positioned packets, aborting map creation")
        return

    # Create map
    m = folium.Map(location=[rxLat, rxLng], zoom_start=15)

    # Find last matched packet, and filter out all others after it
    lastMatchedIdx = None
    for i in range(len(points) - 1, -1, -1):
        if points[i][3]:          # matched flag
            lastMatchedIdx = i
            break
    points = points[:lastMatchedIdx+1]

    startLat, startLong = points[0][1], points[0][2]
    endLat, endLong     = points[-1][1], points[-1][2]

    # Starting point marker
    folium.Marker(
        [startLat, startLong], popup='Start',
        icon=folium.Icon(color='green', icon='play', prefix='fa')
    ).add_to(m)

    # End point marker
    folium.Marker(
        [endLat, endLong], popup='End',
        icon=folium.Icon(color='red', icon='stop', prefix='fa')
    ).add_to(m)

    # Receiver marker
    folium.Marker([rxLat, rxLng], popup='Receiver', icon=folium.Icon(color='blue', icon='tower-broadcast', prefix='fa')
    ).add_to(m)

    # Packet markers (green if matched, red if not)
    for (_, lat, lng, matched) in points:
        folium.CircleMarker(
            location=[lat, lng],
            radius=3,
            color='green' if matched else 'red',
            fill=True,
            fill_opacity=0.7,
        ).add_to(m)
    m.save(outputPath)

def plotDistance(trail, rxLat, rxLng):
    if not trail:
        print("Cannot make distance plot due to empty timeline, aborting.")
        return
    time0 = trail[0][0]
    points = []

    # Append time and distance from receiver to list of points
    for (t, lat, lon, _) in trail:
        points.append({
            'time': t - time0,
            'distance': geodesic((lat, lon), (rxLat, rxLng)).meters
        })

    # DataFrame and seaborn graph
    dist_df = pd.DataFrame(points)

    fig, ax = plt.subplots(figsize=(12, 5))
    sns.lineplot(data=dist_df, x='time', y='distance', ax=ax,
                 linewidth=1.5, color='tab:blue')

    ax.set_xlabel('Time (s)')
    ax.set_ylabel('Distance from receiver (m)')
    ax.set_title('Transmitter distance from receiver over time')
    plt.tight_layout()
    ax.set_rasterized(True)
    return fig

def trimTrailingLosses(timeline):
    lastMatchedIdx = None
    for i in range(len(timeline) - 1, -1, -1):
        if timeline[i][1]:          # received flag (True = matched)
            lastMatchedIdx = i
            break
    
    if lastMatchedIdx is None:
        return timeline, 0          # nothing matched — trim nothing

    trimmed = timeline[:lastMatchedIdx + 1]
    removed = len(timeline) - len(trimmed)
    return trimmed, removed

def buildStatsText(packetMatches, packetLosses, latenciesByType, negativeFlag, outlierWarnIPG, 
                   perValues, rxTimestamps, throughputs):
    lines = []
    TYPE_NAMES = {'18': 'MAP', '19': 'SPAT', '20': 'BSM', '31': 'TIM'}

    def log(text=''):
        lines.append(text)
        print(text)

    total = len(packetMatches) + len(packetLosses)
    per = len(packetLosses) / total * 100 if total > 0 else 0

    log("=== V2X Performance Analysis ===")
    log()
    log(f"Overall PER : {per:.2f}%")
    log(f"Matched     : {len(packetMatches)}")
    log(f"Lost        : {len(packetLosses)}")
    log(f"Total       : {total}")

    log()
    log("=== Latency Stats ===")

    if negativeFlag is True:
        log()
        log("Warning: Negative latency values detected.")
        log("This usually occurs when your devices arent time synchronized.")
        log("As a result, some or all of the latency data may be inaccurate.")

    for msgId, latencies in latenciesByType.items():
        if not latencies:
            continue
        arr = np.array(latencies)
        log()
        log(f"--- {TYPE_NAMES[msgId]} ({len(arr)} packets) ---")
        log(f"  Mean   : {np.mean(arr):.3f} ms")
        log(f"  Median : {np.median(arr):.3f} ms")
        log(f"  P95    : {np.percentile(arr, 95):.3f} ms")
        log(f"  P99    : {np.percentile(arr, 99):.3f} ms")
        log(f"  Std Dev: {np.std(arr):.3f} ms")
        log(f"  Min    : {np.min(arr):.3f} ms")
        log(f"  Max    : {np.max(arr):.3f} ms")

    log()
    log("=== IPG Stats ===")
    
    if outlierWarnIPG is True:
        log()
        log("Warning: Extreme IPG values detected.")
        log("This may indicate a loss of multiple packets.")
        log("Check the IPG and PER stats and the PER graph for more info.")
    
    for msgId, timestamps in rxTimestamps.items():
        if len(timestamps) < 2:
            continue
        gaps = np.diff(sorted(timestamps)) * 1000
        log()
        log(f"--- {TYPE_NAMES[msgId]} ({len(gaps)} gaps) ---")
        log(f"  Mean   : {np.mean(gaps):.3f} ms")
        log(f"  Median : {np.median(gaps):.3f} ms")
        log(f"  P95    : {np.percentile(gaps, 95):.3f} ms")
        log(f"  P99    : {np.percentile(gaps, 99):.3f} ms")
        log(f"  Std Dev: {np.std(gaps):.3f} ms")
        log(f"  Min    : {np.min(gaps):.3f} ms")
        log(f"  Max    : {np.max(gaps):.3f} ms")

    if throughputs:
        arr = np.array(throughputs)
        log()
        log("=== Throughput Stats ===")
        log(f"  Mean : {np.mean(arr):.2f} bps")
        log(f"  Max  : {np.max(arr):.2f} bps")
        log(f"  Min  : {np.min(arr):.2f} bps")

    return '\n'.join(lines)


def saveReport(figures, statsText, outputPath='v2x_report.pdf'):
    with PdfPages(outputPath) as pdf:

        # split stats across multiple pages if needed
        lines = statsText.split('\n')
        linesPerPage = 60  # adjust this based on font size

        for i in range(0, len(lines), linesPerPage):
            chunk = '\n'.join(lines[i : i + linesPerPage])
            fig, ax = plt.subplots(figsize=(8.5, 11))
            ax.axis('off')
            ax.text(0.05, 0.98, chunk, transform=ax.transAxes,
                    fontsize=10, verticalalignment='top', fontfamily='monospace')
            pdf.savefig(fig)
            plt.close(fig)

        # graphs — one per page
        for f in figures:
            pdf.savefig(f)
            plt.close(f)

    wordoutput = 'v2x_report.docx'
    cv = Converter(outputPath)
    cv.convert(wordoutput)
    cv.close()
    print(f"\nReport saved to {outputPath}")

if __name__ == "__main__":
   main()