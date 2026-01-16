import streamlit as st
import requests
import os

# --- 設定 ---
API_KEY = st.secrets["DIFY_API_KEY"]
BASE_URL = "https://api.dify.ai/v1"
FILE_VARIABLE_KEY = "material"

# サーバー側にある固定ファイルのパス
FIXED_FILE_PATH = "CV11.pdf"

headers = {
    "Authorization": f"Bearer {API_KEY}"
}

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
        response.raise_for_status()
        return response.json()
    except Exception as e:
        st.error(f"API通信エラー: {e}")
        return None

# --- UI構築 ---
st.set_page_config(page_title="講義の復習", page_icon="🤖")
st.title("🤖 講義振り返りインタビュアー")

# セッション管理
if "messages" not in st.session_state:
    st.session_state.messages = []
if "conversation_id" not in st.session_state:
    st.session_state.conversation_id = ""

# --- 自動初期化プロセス ---
# 会話IDがまだない（＝アクセス直後）なら、裏でファイルを送って会話を開始する
if not st.session_state.conversation_id:
    with st.spinner("インタビュアーを準備中...（資料を読み込んでいます）"):
        # 1. 固定ファイルをアップロード
        # ユーザーIDはセッションごとにユニークにするのが理想ですが、今回は固定で例示
        file_id = upload_local_file_to_dify(FIXED_FILE_PATH, "guest_user")
        
        if file_id:
            # 2. 会話を開始（トリガー用メッセージ送信）
            initial_res = send_chat_message(
                query="授業内容について学んだことを教えてください。", # 開始トリガー
                conversation_id="",
                uploaded_file_id=file_id
            )
            
            if initial_res:
                st.session_state.conversation_id = initial_res.get('conversation_id')
                # Difyからの最初の質問（「授業内容の〜」に対する応答）を表示
                welcome_msg = initial_res.get('answer', '')
                st.session_state.messages.append({"role": "assistant", "content": welcome_msg})
                # 画面を更新してチャット画面を表示
                st.rerun()

# --- チャット画面 ---
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.write(msg["content"])

if prompt := st.chat_input("ここに入力..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.write(prompt)

    with st.spinner("考え中..."):
        # 2回目以降は file_id 不要
        response = send_chat_message(
            query=prompt,
            conversation_id=st.session_state.conversation_id
        )
        
        if response:
            ans = response.get('answer', '')
            st.session_state.messages.append({"role": "assistant", "content": ans})
            with st.chat_message("assistant"):
                st.write(ans)
