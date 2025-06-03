import os
import re
import subprocess
import requests
from bs4 import BeautifulSoup
from g4f.client import Client  # g4f ラッパーを使用

BASE_URL = 'https://novel18.syosetu.com'
HISTORY_FILE = '小説家になろうR18ダウンロード経歴.txt'
LOCAL_HISTORY_PATH = f'/tmp/{HISTORY_FILE}'
REMOTE_HISTORY_PATH = f'drive:{HISTORY_FILE}'
COOKIES = {'over18': 'yes'}

client = Client()

def fetch_url(url):
    headers = {'User-Agent': 'Mozilla/5.0'}
    return requests.get(url, headers=headers, cookies=COOKIES)

def load_history():
    if not os.path.exists(LOCAL_HISTORY_PATH):
        subprocess.run(['rclone', 'copyto', REMOTE_HISTORY_PATH, LOCAL_HISTORY_PATH], check=False)

    history = {}
    if os.path.exists(LOCAL_HISTORY_PATH):
        with open(LOCAL_HISTORY_PATH, 'r', encoding='utf-8') as f:
            for line in f:
                match = re.match(r'(https?://[^\s|]+)\s*\|\s*(\d+)', line.strip())
                if match:
                    url, last = match.groups()
                    history[url.rstrip('/')] = int(last)
    return history

def save_history(history):
    with open(LOCAL_HISTORY_PATH, 'w', encoding='utf-8') as f:
        for url, last in history.items():
            f.write(f'{url}  |  {last}\n')
    subprocess.run(['rclone', 'copyto', LOCAL_HISTORY_PATH, REMOTE_HISTORY_PATH], check=True)

def safe_filename(title):
    return re.sub(r'[<>:"/\\|?*]', '', title).strip()

def split_text_naturally(text, limit=1500):
    parts, buf = [], ''
    for sentence in re.split('(?<=[。！？])', text):
        if len(buf) + len(sentence) <= limit:
            buf += sentence
        else:
            if buf:
                parts.append(buf)
            buf = sentence
    if buf:
        parts.append(buf)
    return parts

def translate(text):
    try:
        return client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": text}],
        ).choices[0].message.content.strip()
    except Exception as e:
        print(f'翻訳失敗。再試行中: {e}')
        try:
            return client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": text}],
            ).choices[0].message.content.strip()
        except Exception as e:
            print(f'再翻訳失敗: {e}')
            return "[翻訳失敗]"

# URLファイル読み込み
script_dir = os.path.dirname(__file__)
url_file_path = os.path.join(script_dir, '小説家になろうR18.txt')
with open(url_file_path, 'r', encoding='utf-8') as f:
    urls = [line.strip().rstrip('/') for line in f if line.strip().startswith('http')]

history = load_history()

for novel_url in urls:
    try:
        print(f'\n--- 処理開始: {novel_url} ---')
        url = novel_url
        sublist = []

        while True:
            res = fetch_url(url)
            soup = BeautifulSoup(res.text, 'html.parser')
            title_text = safe_filename(soup.find('title').get_text())
            sublist += soup.select('.p-eplist__sublist .p-eplist__subtitle')
            next_page = soup.select_one('.c-pager__item--next')
            if next_page and next_page.get('href'):
                url = f'{BASE_URL}{next_page.get("href")}'
            else:
                break

        download_from = history.get(novel_url, 0)
        sub_len = len(sublist)
        new_max = download_from

        for i, sub in enumerate(sublist):
            if i + 1 <= download_from:
                continue

            sub_title = sub.text.strip()
            link = sub.get('href')
            file_name = f'{i+1:03d}.txt'
            folder_num = (i // 999) + 1
            folder_base = f'/tmp/narouR18_dl/{title_text}/{folder_num:03d}'
            jp_path = os.path.join(folder_base, 'japanese')
            en_path = os.path.join(folder_base, 'english')
            os.makedirs(jp_path, exist_ok=True)
            os.makedirs(en_path, exist_ok=True)

            res = fetch_url(f'{BASE_URL}{link}')
            soup = BeautifulSoup(res.text, 'html.parser')
            sub_body = soup.select_one('.p-novel__body')
            sub_body_text = sub_body.get_text().strip() if sub_body else '[本文が取得できませんでした]'

            # 保存（日本語）
            jp_file_path = os.path.join(jp_path, file_name)
            with open(jp_file_path, 'w', encoding='utf-8') as f:
                f.write(f'{sub_title}\n\n{sub_body_text}')

            # 翻訳処理
            segments = split_text_naturally(sub_body_text)
            translated_segments = [translate(seg) for seg in segments]
            translated_body = '\n'.join(translated_segments)

            # 保存（英語）
            en_file_path = os.path.join(en_path, file_name)
            with open(en_file_path, 'w', encoding='utf-8') as f:
                f.write(f'{sub_title}\n\n{translated_body}')

            print(f'{file_name} saved in folder {folder_num:03d} ({i+1}/{sub_len})')
            new_max = i + 1

        history[novel_url] = new_max

    except Exception as e:
        print(f'エラー発生: {novel_url} → {e}')
        continue

save_history(history)

# Google Driveへアップロード
subprocess.run(['rclone', 'copy', '/tmp/narouR18_dl', 'drive:', '--transfers=4', '--checkers=8', '--fast-list'], check=True)
