import seaborn as sns
import matplotlib.pyplot as plt
# Apply the default theme
sns.set_theme()

fmri = sns.load_dataset("fmri")
sns.relplot(
    data=fmri, kind="line",
    x="timepoint", y="signal", col="region",
    hue="event", style="event",
)
plt.show()