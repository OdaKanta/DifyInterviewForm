import streamlit as st
import requests
import json

# --- 設定 ---
API_KEY =  = st.secrets["DIFY_API_KEY"]
BASE_URL = "https://api.dify.ai/v1"  # オンプレ版の場合はそのURL
# 開始ノードで定義されている変数名（YMLの "variable: material" に対応）
FILE_VARIABLE_KEY = "material" 

# --- ヘッダー設定 ---
headers = {
    "Authorization": f"Bearer {API_KEY}"
}

def upload_file_to_dify(uploaded_file, user_id):
    """
    ファイルをDifyにアップロードし、IDを取得する関数
    """
    url = f"{BASE_URL}/files/upload"
    
    # MIMEタイプに応じたファイル送信準備
    files = {
        'file': (uploaded_file.name, uploaded_file, uploaded_file.type)
    }
    data = {
        'user': user_id
    }
    
    try:
        response = requests.post(url, headers=headers, files=files, data=data)
        response.raise_for_status()
        return response.json().get('id')
    except Exception as e:
        st.error(f"ファイルアップロードエラー: {e}")
        return None

def send_chat_message(query, conversation_id, uploaded_file_id=None, user_id="streamlit_user"):
    """
    Difyにメッセージを送信する関数
    """
    url = f"{BASE_URL}/chat-messages"
    
    # inputs の構築
    inputs = {}
    
    # 初回（ファイルIDがある場合）のみ、inputsにファイル情報をセットする
    if uploaded_file_id:
        inputs[FILE_VARIABLE_KEY] = {
            "type": "document",            # YMLの設定に合わせる（image/document/videoなど）
            "transfer_method": "local_file",
            "upload_file_id": uploaded_file_id
        }

    payload = {
        "inputs": inputs,
        "query": query,
        "response_mode": "blocking", # ストリーミングしたい場合は 'streaming'
        "conversation_id": conversation_id,
        "user": user_id,
    }

    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        st.error(f"APIエラー: {e}")
        return None

# --- Streamlit UI ---
st.title("🤖 講義振り返りインタビュアー")

# セッション状態の初期化
if "conversation_id" not in st.session_state:
    st.session_state.conversation_id = ""
if "file_uploaded" not in st.session_state:
    st.session_state.file_uploaded = False
if "messages" not in st.session_state:
    st.session_state.messages = []

# サイドバーでファイルアップロード
st.sidebar.header("講義資料の提出")
uploaded_file = st.sidebar.file_uploader("講義資料(PDF)をアップロードしてください", type=["pdf"])

if uploaded_file and not st.session_state.file_uploaded:
    with st.spinner("資料を読み込んでいます..."):
        # 1. Difyへファイルをアップロード
        file_id = upload_file_to_dify(uploaded_file, "streamlit_user")
        
        if file_id:
            # 2. アップロード成功後、Difyのフローを開始（最初のトリガー）
            #    YMLでは最初の質問生成にユーザー入力が必要なフローに見えますが、
            #    開始ノード通過のために空文字や挨拶を送ってフローをキックします。
            initial_response = send_chat_message(
                query="授業内容について学んだことを教えてください。", # 初期トリガー用テキスト
                conversation_id="",
                uploaded_file_id=file_id
            )
            
            if initial_response:
                st.session_state.conversation_id = initial_response.get('conversation_id')
                st.session_state.file_uploaded = True
                
                # Difyからの最初の質問を表示
                answer = initial_response.get('answer', '')
                st.session_state.messages.append({"role": "assistant", "content": answer})
                st.rerun()

# チャット履歴の表示
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.write(msg["content"])

# ユーザー入力
if prompt := st.chat_input("回答を入力してください"):
    # ユーザーのメッセージを表示
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.write(prompt)

    # Difyへ送信（2回目以降なので file_id は不要）
    with st.spinner("考え中..."):
        response = send_chat_message(
            query=prompt,
            conversation_id=st.session_state.conversation_id
        )
        
        if response:
            answer = response.get('answer', '')
            st.session_state.messages.append({"role": "assistant", "content": answer})
            with st.chat_message("assistant"):
                st.write(answer)
