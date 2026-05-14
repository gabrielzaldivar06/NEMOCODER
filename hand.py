import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

fig, ax = plt.subplots()
ax.set_xlim(0, 10)
ax.set_ylim(0, 10)
ax.set_aspect('equal', adjustable='box')
ax.set_title("Improved Hand Drawing")

skin_color = (0.8, 0.6, 0.4)
line_color = (0.3, 0.3, 0.3)
shadow_color = (0.5, 0.5, 0.5)

# Palm (simplified shape)
palm = plt.Polygon([[4, 4], [6, 4], [5, 7], [4, 7]], closed=True, facecolor=skin_color, edgecolor=line_color, linewidth=2)
ax.add_patch(palm)

# Fingers (simplified shapes)
finger1 = plt.Polygon([[3.5, 4], [4.5, 6], [5.5, 7], [4.5, 6]], closed=True, facecolor=skin_color)
finger2 = plt.Polygon([[4.5, 4], [5.5, 6], [6.5, 7], [5.5, 6]], closed=True, facecolor=skin_color)

ax.add_patch(finger1)
ax.add_patch(finger2)

plt.savefig('hand.png')
plt.close()