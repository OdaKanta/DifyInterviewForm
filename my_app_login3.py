import streamlit as st
import requests
import os
import datetime
import pandas as pd
from streamlit_gsheets import GSheetsConnection

# --- スプレッドシート接続 ---
conn = st.connection("gsheets", type=GSheetsConnection)

# --- 設定 ---
API_KEY = st.secrets["DIFY_API_KEY"]
BASE_URL = "https://api.dify.ai/v1"
FILE_VARIABLE_KEY = "material"

# サーバー側にある固定ファイルのパス
FIXED_FILE_PATH = "NLP11.pdf"

headers = {
    "Authorization": f"Bearer {API_KEY}"
}

# --- ログイン機能 ---
def login():
    """簡易ログイン機能: ユーザー名が未入力なら入力を求める"""
    if "username" not in st.session_state:
        st.session_state.username = None

    if not st.session_state.username:
        with st.form("login_form"):
            st.write("学習を開始するにはID（氏名または学籍番号）を入力してください")
            username_input = st.text_input("ユーザーID")
            submitted = st.form_submit_button("開始")
            if submitted and username_input:
                st.session_state.username = username_input
                st.rerun()
        st.stop() # ログインするまでここで処理を止める

def upload_local_file_to_dify(file_path, user_id):
    """
    ローカル（サーバー上）のファイルを読み込んでDifyに送信する
    """
    if not os.path.exists(file_path):
        st.error(f"ファイルが見つかりません: {file_path}")
        return None

    url = f"{BASE_URL}/files/upload"
    
    # バイナリモードでファイルを開く
    with open(file_path, "rb") as f:
        files = {
            'file': (os.path.basename(file_path), f, 'application/pdf') # ファイル名とMIMEタイプを指定
        }
        data = {'user': user_id}
        
        try:
            response = requests.post(url, headers=headers, files=files, data=data)
            response.raise_for_status()
            return response.json().get('id')
        except Exception as e:
            st.error(f"内部アップロードエラー: {e}")
            return None

def send_chat_message(query, conversation_id, uploaded_file_id=None, user_id="streamlit_student"):
    url = f"{BASE_URL}/chat-messages"
    inputs = {}
    
    # ファイルIDがある場合（初回）のみinputsにセット
    if uploaded_file_id:
        inputs[FILE_VARIABLE_KEY] = {
            "type": "document",
            "transfer_method": "local_file",
            "upload_file_id": uploaded_file_id
        }

    payload = {
        "inputs": inputs,
        "query": query,
        "response_mode": "blocking",
        "conversation_id": conversation_id,
        "user": user_id,
    }

    try:
        response = requests.post(url, headers=headers, json=payload)
        
        # エラーがある場合は詳細を表示して例外を投げる
        if response.status_code != 200:
            st.error(f"APIエラー: {response.status_code}")
            st.code(response.text) # Difyからの生のエラーメッセージを表示
            
        response.raise_for_status()
        return response.json()
        
    except Exception as e:
        # すでに上で表示しているのでここではシンプルな表示にとどめるか、何もしない
        return None

# --- ログ保存機能 ---
def save_log_to_sheet(username, user_input, full_response, conversation_id):
    """会話ログをGoogleスプレッドシートに保存する"""
    try:
        # 現在の時刻
        now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S')
        
        # ttl=0 を追加してキャッシュを無効化し、常に最新のスプレッドシートを読み込む
        existing_data = conn.read(
            spreadsheet=st.secrets["spreadsheet_url"], 
            ttl=0
        )
        
        # 新しい行を作成
        new_row = {
            "date": now,
            "user_id": username,
            "user_input": user_input,
            "ai_response": full_response,
            "conversation_id": conversation_id
        }
        
        # データを追記
        new_row_df = pd.DataFrame([new_row])
        
        # 既存データが空の場合でも動くように処理
        if existing_data.empty:
            updated_df = new_row_df
        else:
            updated_df = pd.concat([existing_data, new_row_df], ignore_index=True)
        
        # スプレッドシートを更新
        conn.update(spreadsheet=st.secrets["spreadsheet_url"], data=updated_df)
        
    except Exception as e:
        st.error(f"ログ保存エラー: {e}")

# --- UI構築 ---
st.set_page_config(page_title="講義の復習", page_icon="🤖")
st.title("🤖 講義振り返りインタビュアー")

# --- 最初にログインチェックを実行 ---
login()
current_user = st.session_state.username
st.sidebar.write(f"ログイン中: {current_user}")

# セッション管理（既存通り）
if "messages" not in st.session_state:
    st.session_state.messages = []
if "conversation_id" not in st.session_state:
    st.session_state.conversation_id = ""

# --- 自動初期化プロセス ---
if not st.session_state.conversation_id:
    with st.spinner("インタビュアーを準備中...（資料を読み込んでいます）"):
        file_id = upload_local_file_to_dify(FIXED_FILE_PATH, current_user)
        
        if file_id:
            initial_res = send_chat_message(
                query="授業内容について学んだことを教えてください。", 
                conversation_id="",
                uploaded_file_id=file_id,
                user_id=current_user  # 【重要】アップロードと同じIDを指定
            )
            
            if initial_res:
                st.session_state.conversation_id = initial_res.get('conversation_id')
                welcome_msg = initial_res.get('answer', '')
                st.session_state.messages.append({"role": "assistant", "content": welcome_msg})
                st.rerun()
        
        else:
            st.error("ファイルのアップロードに失敗しました。ユーザーIDを確認してください。")

# チャット画面表示（既存通り）
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.write(msg["content"])

# ユーザー入力処理
if prompt := st.chat_input("ここに入力..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.write(prompt)

    with st.spinner("考え中..."):
        # 変更: user_id にログインユーザー名を渡す
        response = send_chat_message(
            query=prompt,
            conversation_id=st.session_state.conversation_id,
            user_id=current_user
        )
        
        if response:
            ans = response.get('answer', '')
            st.session_state.messages.append({"role": "assistant", "content": ans})
            with st.chat_message("assistant"):
                st.write(ans)
            
            # --- スプレッドシートへログ保存 ---
            save_log_to_sheet(
                username=current_user,
                user_input=prompt,
                full_response=ans,
                conversation_id=st.session_state.conversation_id
            )
