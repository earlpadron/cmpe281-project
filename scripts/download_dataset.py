import os
import pandas as pd
import requests
import random
from tqdm import tqdm # this is to show a progress bar

def download_unsplash_sample(csv_path, output_dir="../images", num_images = 1000):
    """Download a random sample of images from the Unslpash like dataset CSV file """
    # create output directory
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        print(f"Created output directory: {output_dir}")

    # load the CSV 
    print(f"Loading the dataset from {csv_path}...")
    try:
        # the "photo_image_url" column contains the download links
        df = pd.read_csv(csv_path, sep='\t', usecols=['photo_id', 'photo_image_url'])
    except Exception as e:
        print(f"Error loading CSV: {e}")
        return
    
    # shuffle and pick a random sample 
    available_photos = df.to_dict(orient='records')
    random.shuffle(available_photos)

    count = 0
    print(f"Starting to download {num_images} images...")

    for photo in tqdm(available_photos, desc="Downloading images",total=num_images):
        if count >= num_images:
            break

        photo_id = photo['photo_id']
        image_url = photo['photo_image_url']

        # append the parameters to the URL to get a high-quality version (w=1600)
        # but we can also use the raw url
        download_url = f"{image_url}?w=1600&q=80"
        file_path = os.path.join(output_dir, f"{photo_id}.jpg")

        # skip if already downlaoded
        if(os.path.exists(file_path)):
            print(f"Image {file_path} already exists, skipping...")
            count += 1
            continue

        try:
            response = requests.get(download_url, timeout=10)
            if(response.status_code == 200 and 'image' in response.headers.get('Content-Type', '')):
                with open(file_path, 'wb') as f:
                    f.write(response.content)
                count += 1
        except Exception as e:
           #skip any errors and continue with the next image
            continue

    print(f"Finished! Total images downloaded: {count}")

if __name__ == "__main__":
    # update this path to actualy csv location
    CSV_LOCATION = "../unsplash-research-dataset-lite-latest/photos.csv000"
    download_unsplash_sample(csv_path=CSV_LOCATION, output_dir="../images", num_images=1000)