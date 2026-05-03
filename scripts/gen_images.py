from PIL import Image
import numpy as np
import random
from pathlib import Path

output_dir = Path("test_images")
output_dir.mkdir(exist_ok=True)

for i in range(100):  # generate 100 images
    width = random.randint(200, 2000)
    height = random.randint(200, 2000)

    # random noise image
    data = np.random.randint(0, 255, (height, width, 3), dtype=np.uint8)
    img = Image.fromarray(data, 'RGB')

    filename = output_dir / f"img_{i}.jpg"
    img.save(filename, quality=random.randint(50, 95))

print("Generated 100 random images")