import os
import re
import requests
from bs4 import BeautifulSoup

def extract_and_download_photos(cemetery_id, memorial_id, html_content):
    """
    Parses stashed HTML content, extracts Find a Grave photo URLs,
    and downloads them locally into photos/<cemetery_id>/ named by memorial_id.
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    
    photo_urls = []
    
    def is_target_photo(url):
        # Extract filename from path (last component)
        filename = url.split('/')[-1]
        return filename.startswith(f"{memorial_id}_") or filename.startswith(f"{memorial_id}.")

    # 1. Identify primary profile image
    profile_img = soup.find(id='profileImage')
    if not profile_img:
        profile_img = soup.find('img', class_='profileImage')
        
    primary_url = None
    if profile_img:
        src = profile_img.get('src') or profile_img.get('data-src')
        if src and 'images.findagrave.com/photos/' in src:
            primary_url = src.split('?')[0]
            if is_target_photo(primary_url):
                photo_urls.append(primary_url)
            
    # 2. Extract other img tags
    for img in soup.find_all('img'):
        for attr in ['src', 'data-src']:
            val = img.get(attr)
            if val and 'images.findagrave.com/photos/' in val:
                clean_url = val.split('?')[0]
                if clean_url not in photo_urls and is_target_photo(clean_url):
                    photo_urls.append(clean_url)
                    
    # 3. Extract original image link tags
    for a in soup.find_all('a'):
        href = a.get('href')
        if href and 'images.findagrave.com/photos/' in href:
            clean_url = href.split('?')[0]
            if clean_url not in photo_urls and is_target_photo(clean_url):
                photo_urls.append(clean_url)
                
    # Decompose soup tree to release RAM
    soup.decompose()
    
    if not photo_urls:
        return []
        
    # Ensure destination directory exists
    dest_dir = os.path.join('photos', str(memorial_id))
    os.makedirs(dest_dir, exist_ok=True)
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    
    downloaded_paths = []
    
    for idx, url in enumerate(photo_urls):
        ext = '.jpg'
        if url.lower().endswith('.png'):
            ext = '.png'
        elif url.lower().endswith('.jpeg'):
            ext = '.jpeg'
        elif url.lower().endswith('.gif'):
            ext = '.gif'
            
        if url == primary_url:
            filename = f"primary{ext}"
        else:
            filename = f"{idx+1}{ext}"
            
        dest_path = os.path.join(dest_dir, filename)
        
        # Skip if already downloaded
        if os.path.exists(dest_path) and os.path.getsize(dest_path) > 0:
            downloaded_paths.append(dest_path)
            continue
            
        try:
            r = requests.get(url, headers=headers, timeout=15)
            if r.status_code == 200:
                with open(dest_path, 'wb') as f:
                    f.write(r.content)
                downloaded_paths.append(dest_path)
        except Exception:
            pass # Silently proceed on connection failure
            
    return downloaded_paths
