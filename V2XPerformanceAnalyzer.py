from lxml import etree
import time
import pandas as pd
import seaborn as sns
import sys
import matplotlib.pyplot as plt
import numpy as np

def main():
    # Attempt to grab the files from Tx and Rx
    txFile = None
    rxFile = None
    try:
        args = sys.argv[1:]

        if len(args) == 2:
        # pure Tx file, no MAC needed
            txFile = args[0]
            rxFile = args[1]

        elif len(args) == 3:
        # combined Tx file, filter by MAC
            txList, _ = separateByAddress(etree.parse(args[0]).getroot(), args[2])
            rxFile = args[1]

    except IndexError:
       print("Error: Please specify a pdml file for both a transmitter and a receiver, and a MAC or IPv6 address, if applicable.")
       sys.exit(1)
    
    print("Parsing Rx File...")
    rxTree = etree.parse(rxFile)
    print("Done!")
    if txFile:
        print("Parsing Tx File...")
        txList = list(etree.parse(txFile).getroot())
        print("Done!")
    print("Building Rx map...")
    rxMap = buildRxMap(rxTree.getroot())
    print("Done!")
    packetMatches = []
    packetLosses = []

    # Get initial characteristics from each packet
    initialReqs = {'frame.len', 'frame.time_delta_displayed', 'frame.time_epoch', 'frame.number', 'j2735.messageId', 'frame.len'}
    
    # Iteration Matching Test
    for packet in txList:
        # Obtain necessary starting fields from Tx packet
        txFields = get_field_values(packet, initialReqs)
        txType = txFields['j2735.messageId']
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
    
    # Fill lists with necessary data for metric calculations
    for (packet, matchingPacket, latency, txType) in packetMatches:
        packetFields = get_field_values(packet, initialReqs)
        matchFields = get_field_values(matchingPacket, initialReqs)
        
        packetTimeline.append((float(packetFields['frame.time_epoch']), True))
        
        rxTimestamps[txType].append(float(matchFields['frame.time_epoch']))

        packetLengths.append(((float(matchFields['frame.time_epoch'])), (int(matchFields['frame.len']))))
    
    # Packet loss test and more data for PER
    for packet in packetLosses:
        packetFields = get_field_values(packet, initialReqs)
        
        print("Tx packet "+ str(packetFields['frame.number'])+ " was lost")
        
        packetTimeline.append((float(packetFields['frame.time_epoch']), False))

    packetTimeline = sorted(packetTimeline)
    
    # Perform final metric calculation and analysis
    calculatePER(packetTimeline)
    calculateLatency(packetMatches)
    calculateIPG(rxTimestamps)
    calculateThroughput(packetLengths)

def separateByAddress(tree, txMac):
    matchesAddress = []
    notMatchesAddress = []
    
    macFields = {'ipv6.src', 'wlan.sa'}
    
    for packet in tree:
        fields = get_field_values(packet, macFields)
        srcMac = fields.get('ipv6.src') or fields.get('wlan.sa')
        
        if srcMac is None:
            continue
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
    


def matchTIM(txPacket, rxMap, cutoffTime=3):
    TIMrequiredFields = {'j2735.msgCnt', 'j2735.lat_03', 'j2735.long_01', 'frame.time_epoch'}

    txFields = get_field_values(txPacket, TIMrequiredFields)
    key = (make_key(txFields.get('j2735.msgCnt')), make_key(txFields.get('j2735.lat_03')), 
            make_key(txFields.get('j2735.long_01')))

    matches = rxMap['31'].get(key, [])
    return findBestMatch(txPacket, matches, cutoffTime)

def matchBSM(txPacket, rxMap, cutoffTime=3):
    BSMrequiredFields = {'j2735.msgCnt', 'j2735.secMark', 'j2735.id', 'frame.time_epoch'}
    
    txFields = get_field_values(txPacket, BSMrequiredFields)

    key = (make_key(txFields.get('j2735.msgCnt')), make_key(txFields.get('j2735.secMark')), 
            make_key(txFields.get('j2735.id')))
    
    matches = rxMap['20'].get(key, [])

    return findBestMatch(txPacket, matches, cutoffTime)


def matchSPAT(txPacket, rxMap, cutoffTime=3):
    SPATrequiredFields = {'j2735.id_01', 'j2735.signalGroup', 'j2735.minEndTime_01', 'frame.time_epoch'}

    txFields = get_field_values(txPacket, SPATrequiredFields)
    groups = list_check(txFields.get('j2735.signalGroup'))
    times  = list_check(txFields.get('j2735.minEndTime_01'))
    
    key = (make_key(txFields.get('j2735.id_01')), frozenset(zip(groups, times)))
    
    matches = rxMap['19'].get(key, [])
    return findBestMatch(txPacket, matches, cutoffTime)
    

def matchMAP(txPacket, rxMap, cutoffTime=0.5):
    MAPrequiredFields = {'j2735.id_01', 'j2735.lat_03', 'j2735.long_01', 'frame.time_epoch'}
    
    txFields = get_field_values(txPacket, MAPrequiredFields)

    key = (make_key(txFields.get('j2735.id_01')), make_key(txFields.get('j2735.lat_03')), 
           make_key(txFields.get('j2735.long_01')))
    
    matches = rxMap['18'].get(key, [])
    
    return findBestMatch(txPacket, matches, cutoffTime)
    
# Using packet timestamps, find the best possible match for the given Tx packet if multiple matches exist
def findBestMatch(txPacket, matches, cutoffTime):
    if matches:
        txTime = float(txPacket.find(".//field[@name='frame.time_epoch']").get('show'))
        minDiff = cutoffTime
        bestMatch = None
        
        for packet in matches:
            rxTime = float(packet.find(".//field[@name='frame.time_epoch']").get('show'))
            diff = rxTime - txTime
            
            if(diff > 0) and (diff < cutoffTime) and (diff < minDiff):
                minDiff = diff
                bestMatch = packet
        
        if(bestMatch is not None):
            return (bestMatch, minDiff)
        else:
            return None
    else:
        return None

def calculatePER(txPackets, windowSize=50):
    if(len(txPackets) == 0):
        print("No packets found, aborting PER calculation!")
        return None
    windows = []
    initialTime = txPackets[0]
    for i in range(len(txPackets)-windowSize + 1):
        window = txPackets[i : i + windowSize]
        windowTime = window[-1][0]
        losses = sum(1 for _, received in window if received == False)
        per = losses / windowSize * 100
        windows.append((windowTime, per))
        print(f"  t={windowTime:.3f}  PER={per:.1f}%  ({losses}/{windowSize} lost)")
    
    timeList, perList = map(list, zip(*windows))
    per_df = pd.DataFrame({'time': timeList, 'PER': perList})
    sns.lineplot(data=per_df, x='time', y='PER')
    plt.show()
    

def calculateLatency(packetMatches):
    if(len(packetMatches) == 0):
        print("No packets found, aborting Latency calculation!")
        return None
    TYPE_NAMES = {'18': 'MAP', '19': 'SPAT', '20': 'BSM', '31': 'TIM'}
    latenciesByType = {'18': [], '19': [], '20': [], '31': []}
    latency_data = []
    for (txPacket, _, latency, txType) in packetMatches:
        latenciesByType[txType].append(latency * 1000) 

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
    
    latency_df = pd.DataFrame(latency_data)
    fig, ax = plt.subplots(figsize=(8, 5))
    sns.ecdfplot(data=latency_df, x='latency', hue='type', ax=ax, linewidth=2)
    ax.set_title("Latency CDF by message type")
    ax.set_xlabel('Latency (ms)')
    ax.set_ylabel('Cumulative proportion')
    plt.show()

def calculateIPG(packetTimestamps):
    TYPE_NAMES = {'18': 'MAP', '19': 'SPAT', '20': 'BSM', '31': 'TIM'}
    ipg_data = []
    for msgType, packets in packetTimestamps.items():
        if len(packets) < 2:
            continue
        else:
            timestamps = np.array(packets)
            timestamps.sort()
            gaps = np.diff(timestamps) * 1000
            for packetGap in gaps:
                ipg_data.append({'IPG': packetGap, 'type': TYPE_NAMES.get(msgType)})
            print(f"\n--- IPG Stats for message type {msgType}: ---")
            print(f"  Mean   : {np.mean(gaps):.3f} ms")
            print(f"  Median : {np.median(gaps):.3f} ms")
            print(f"  P95    : {np.percentile(gaps, 95):.3f} ms")
            print(f"  P99    : {np.percentile(gaps, 99):.3f} ms")
            print(f"  Min    : {np.min(gaps):.3f} ms")
            print(f"  Max    : {np.max(gaps):.3f} ms")
            print(f"  Std Dev: {np.std(gaps):.3f} ms")
    ipg_df = pd.DataFrame(ipg_data)
    fig, ax = plt.subplots(figsize=(8, 5))
    sns.ecdfplot(data=ipg_df, x='IPG', hue='type', ax=ax, linewidth=2)
    ax.set_title("Inter-packet Gap (IPG) by message type")
    ax.set_xlabel('IPG (ms)')
    ax.set_ylabel('Cumulative proportion')
    plt.show()

def calculateThroughput(packetLengths, winSeconds=5):
    if not packetLengths:
        return "no matched packets"
    packetLengths.sort()
    tStart = packetLengths[0][0]
    tEnd = packetLengths[-1][0]

    winStart = tStart
    throughputs = []
    windowTimes = []

    while winStart + winSeconds <= tEnd:
        winEnd = winStart + winSeconds
        bytes = sum(length for time, length in packetLengths if winStart <= time < winEnd)
        throughputs.append((bytes * 8) / winSeconds)
        windowTimes.append(winStart - tStart)
        winStart += winSeconds
    
    if throughputs:
        arr = np.array(throughputs)
        print("\n --- Throughput Statistics ---")
        print(f"  Mean : {np.mean(arr):.2f} bps")
        print(f"  Max  : {np.max(arr):.2f} bps")
        print(f"  Min  : {np.min(arr):.2f} bps")

    throughput_df = pd.DataFrame({
        'time': windowTimes,
        'throughput': throughputs
    })

    fig, ax = plt.subplots(figsize=(10, 5))

    sns.lineplot(data=throughput_df, x='time', y='throughput', ax=ax, linewidth=2, drawstyle='steps-post')

    ax.set_xlabel('Time (s)')
    ax.set_ylabel('Throughput (bps)')
    ax.set_title('Throughput over time (5s windows)')

    plt.tight_layout()
    plt.savefig('throughput.png', dpi=150)
    plt.show()

if __name__ == "__main__":
   main()