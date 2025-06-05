import os
import re
import time
import requests
import subprocess
from bs4 import BeautifulSoup
from g4f.client import Client

client = Client()

BASE_URL = "https://kakuyomu.jp"
HISTORY_FILE = "カクヨムダウンロード経歴.txt"
LOCAL_HISTORY_PATH = f"/tmp/{HISTORY_FILE}"
REMOTE_HISTORY_PATH = f"drive:{HISTORY_FILE}"
DOWNLOAD_DIR_BASE = "/tmp/kakuyomu_dl"

os.makedirs(DOWNLOAD_DIR_BASE, exist_ok=True)


def translate_text(japanese_text):
    """
    GPT-4o-mini (g4f) を使って日本語→英語に翻訳し、
    小説調の自然な文体を維持します。
    """
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "system",
                "content": (
                    "You are a professional translator specializing in Japanese fantasy novels. "
                    "Translate the following passage into natural, expressive English that preserves the original tone, atmosphere, and character voices. "
                    "Do NOT translate the title or include any headers. Output only the translated body."
                )
            }, {
                "role": "user",
                "content": japanese_text
            }],
            web_search=False
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"[Translation failed: {e}]"


def load_history():
    """
    リモート（Drive）から履歴ファイルを取得し、
    ローカルに保存して辞書で返します。
    形式: 「小説URL | 最終ダウンロード話数」
    """
    if not os.path.exists(LOCAL_HISTORY_PATH):
        # ローカルにない場合はリモートからコピーしてみる
        subprocess.run(['rclone', 'copyto', REMOTE_HISTORY_PATH, LOCAL_HISTORY_PATH], check=False)

    history = {}
    if os.path.exists(LOCAL_HISTORY_PATH):
        with open(LOCAL_HISTORY_PATH, 'r', encoding='utf-8') as f:
            for line in f:
                match = re.match(r'(https?://[^\s|]+)\s*\|\s*(\d+)', line.strip())
                if match:
                    url, last = match.groups()
                    history[url] = int(last)
    return history


def save_history(history):
    """
    ローカルの履歴ファイルを更新し、リモート（Drive）にも上書きコピーします。
    """
    with open(LOCAL_HISTORY_PATH, 'w', encoding='utf-8') as f:
        for url, last in history.items():
            f.write(f'{url}  |  {last}\n')
    subprocess.run(['rclone', 'copyto', LOCAL_HISTORY_PATH, REMOTE_HISTORY_PATH], check=True)


def get_novel_title(novel_url):
    """
    小説トップページから <title> タグを拾い、
    「―カクヨム…」以降を省いてかつファイル名に使えるように整形して返します。
    """
    response = requests.get(novel_url)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    title_tag = soup.find("title")
    if title_tag:
        title_text = title_tag.text.strip()
        # 「　− カクヨム …」以降を除去
        title_text = re.sub(r'\s*[-ー]?\s*カクヨム.*$', '', title_text)
        return title_text
    else:
        return "タイトルなし"


def get_episode_links(novel_url):
    """
    小説ページのHTMLソース中から JSON 風のパターンを正規表現で抜き出し、
    (エピソードURL, エピソードタイトル) のタプル一覧を返します。
    """
    response = requests.get(novel_url)
    response.raise_for_status()
    body = response.text
    print("小説情報を取得中…")

    ep_pattern = r'"__typename":"Episode","id":"(.*?)","title":"(.*?)"'
    matches = re.findall(ep_pattern, body)

    if not matches:
        print("指定されたページからエピソード情報を取得できませんでした。")
        return []

    base_url_match = re.match(r"(https://kakuyomu.jp/works/\d+)", novel_url)
    if not base_url_match:
        print("小説のURLからベースURLを抽出できませんでした。")
        return []

    base_url = base_url_match.group(1)
    # f"{base_url}/episodes/{episode_id}" の形に整形
    return [(f"{base_url}/episodes/{ep_id}", ep_title) for ep_id, ep_title in matches]


def download_episode(episode_url, episode_title, novel_title, index):
    """
    １話分の本文（日本語）を取得し、ローカルに保存。
    その後 GPT で英訳し、英訳結果も保存。保存フォルダ構成は以下：

    /tmp/kakuyomu_dl/
      └── <safe_title>/
            └── <folder_num 001～>/
                  ├── japanese/001.txt
                  └── english/001.txt

    - index: 0始まりの連番。episode_number = index + 1 で 1始まりにする。
    - folder_num = (index // 999) + 1 で 999話ごとのフォルダにまとめる。
    """
    response = requests.get(episode_url)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    body = soup.select_one("div.widget-episodeBody").get_text("\n", strip=True)

    folder_num = (index // 999) + 1
    folder_name = f"{folder_num:03d}"
    file_name = f"{index + 1:03d}.txt"

    # ファイル・フォルダ名として安全な小説タイトル
    safe_title = re.sub(r'[\\/*?:"<>|]', '_', novel_title)[:30]
    base_path = os.path.join(DOWNLOAD_DIR_BASE, safe_title, folder_name)

    # japanese/ english フォルダを作成
    for lang in ("japanese", "english"):
        path_lang = os.path.join(base_path, lang)
        os.makedirs(path_lang, exist_ok=True)

    # 1) 日本語本文を保存
    ja_path = os.path.join(base_path, "japanese", file_name)
    with open(ja_path, "w", encoding="utf-8") as f:
        f.write(f"{episode_title}\n\n{body}")

    # 2) 英訳して保存
    translated_body = translate_text(body)
    en_path = os.path.join(base_path, "english", file_name)
    with open(en_path, "w", encoding="utf-8") as f:
        f.write(f"{episode_title}\n\n{translated_body}")

    # この話が保存された「フォルダ」（小説タイトル配下の <folder_num>）を返す
    return os.path.join(DOWNLOAD_DIR_BASE, safe_title)


def upload_novel_to_drive(local_novel_path, safe_title):
    """
    小説全体のローカルフォルダ (例: /tmp/kakuyomu_dl/<safe_title>)
    を Drive の「drive:<safe_title>」へ rclone copy でアップロードします。
    既に存在するファイルは差分同期されるため、累積でOK。
    """
    try:
        print(f"→ Google Drive にアップロード: {safe_title} フォルダ")
        subprocess.run([
            'rclone', 'copy',
            local_novel_path,
            f'drive:{safe_title}',
            '--transfers=4', '--checkers=8', '--fast-list'
        ], check=True)
    except subprocess.CalledProcessError as e:
        print(f"アップロード失敗: {e}")


def download_novels(urls, history):
    """
    すべての小説 URL を順に処理し、

    - 未ダウンロードの話だけを取得・翻訳してローカルに保存
    - 各話ダウンロード後に履歴を即時更新＆保存
    - 10話ごとに当該小説フォルダを Drive にアップロード
    - 300話ごとに 30 秒休憩
    - 最後に残った分もまとめてアップロード
    """
    for novel_url in urls:
        try:
            print(f"\n--- 処理開始: {novel_url} ---")
            # 小説タイトルを取得・ファイル名用に整形
            original_title = get_novel_title(novel_url).strip()
            safe_title = re.sub(r'[\\/*?:"<>|]', '_', original_title)

            # エピソード一覧を取得
            episode_links = get_episode_links(novel_url)
            download_from = history.get(novel_url, 0)

            # 履歴上の「最後にアップロードした話数」を別途保持
            last_uploaded_count = download_from
            new_downloaded = 0

            novel_local_path = os.path.join(DOWNLOAD_DIR_BASE, safe_title)
            # 小説フォルダ全体がなければ作成（後で中に 001/japanese～ ができる）
            os.makedirs(novel_local_path, exist_ok=True)

            for i, (episode_url, episode_title) in enumerate(episode_links):
                episode_number = i + 1
                # 既に取得済みの話はスキップ
                if episode_number <= download_from:
                    continue

                print(f"{episode_number:03d}_{episode_title} をダウンロードして翻訳中…")
                # 1話分をローカルに保存し、"novel_local_path" を返す
                download_episode(episode_url, episode_title, original_title, i)

                # 履歴を即時更新
                history[novel_url] = episode_number
                save_history(history)
                new_downloaded += 1

                # 10話ごとにアップロード
                if (episode_number - last_uploaded_count) >= 10:
                    upload_novel_to_drive(novel_local_path, safe_title)
                    last_uploaded_count = episode_number

                # 300話ごとに 30秒休憩
                if episode_number % 300 == 0:
                    print(f"{episode_number}話ダウンロード完了。30秒休憩します…")
                    time.sleep(30)

            # 最後に残っている（10話区切りに満たない分）をアップロード
            if new_downloaded > 0 and (history[novel_url] - last_uploaded_count) > 0:
                upload_novel_to_drive(novel_local_path, safe_title)

        except Exception as e:
            print(f"エラー発生: {novel_url} → {e}")
            continue


# ==== メイン処理 ====
if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))
    url_file_path = os.path.join(script_dir, 'カクヨム.txt')

    # カクヨム.txt に書かれた小説URLを読み込む
    with open(url_file_path, 'r', encoding='utf-8') as f:
        urls = [
            line.strip().rstrip('/')
            for line in f
            if line.strip().startswith('http')
        ]

    history = load_history()
    download_novels(urls, history)
    # 最後にもう一度念のため全履歴を保存
    save_history(history)
