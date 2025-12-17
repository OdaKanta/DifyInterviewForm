import streamlit as st
import streamlit_authenticator as stauth
import requests
import json
import yaml # 設定保存用

# --- 1. ユーザー情報の設定 ---
names = ["田中 太郎", "佐藤 花子"]
usernames = ["tanaka", "sato"]
passwords = ["pass123", "pass456"]

# ログイン部品の準備
authenticator = stauth.Authenticate(
    {'usernames': {
        usernames[0]: {'name': names[0], 'password': passwords[0]},
        usernames[1]: {'name': names[1], 'password': passwords[1]}
    }},
    "dify_app_cookie", # クッキー名
    "signature_key",   # 署名キー
    cookie_expiry_days=30
)

# --- 2. ログイン画面の表示 ---
authenticator.login('main')

# ログイン状態のチェック
if st.session_state["authentication_status"] == False:
    st.error('ユーザー名またはパスワードが間違っています')
elif st.session_state["authentication_status"] == None:
    st.warning('ユーザー名とパスワードを入力してください')
elif st.session_state["authentication_status"]:
    # ログイン成功時の情報を取得
    username = st.session_state["username"]
    name = st.session_state["name"]

    # --- 3. ログイン成功後のメインコンテンツ ---
    with st.sidebar:
        st.write(f"ようこそ、{name} さん")
        authenticator.logout('ログアウト', 'sidebar')
        
        # デバッグ用：現在の会話IDを表示（不要なら消してもOK）
        if "conversation_id" in st.session_state:
            st.caption(f"Conversation ID: {st.session_state.conversation_id}")

    st.title("認証済みチャットアプリ")

    # 会話履歴と会話IDの初期化
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "conversation_id" not in st.session_state:
        st.session_state.conversation_id = "" # 最初は空っぽ

    # 過去のメッセージを表示
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    # ユーザーが入力した時の処理
    if prompt := st.chat_input("メッセージを入力..."):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            response_placeholder = st.empty()
            full_response = ""

            # API設定
            API_KEY = st.secrets["DIFY_API_KEY"]
            BASE_URL = "https://api.dify.ai/v1/chat-messages"
            headers = {
                "Authorization": f"Bearer {API_KEY}".strip(),
                "Content-Type": "application/json"
            }

            # 送信データ（会話IDを含める）
            data = {
                "inputs": {},
                "query": prompt,
                "response_mode": "streaming",
                "user": username,
                "conversation_id": st.session_state.conversation_id # 保存されているIDを送信
            }

            # Dify APIへのリクエスト
            response = requests.post(BASE_URL, headers=headers, json=data, stream=True)

            # ストリーミング処理
            for line in response.iter_lines():
                if line:
                    decoded_line = line.decode('utf-8')
                    if decoded_line.startswith('data: '):
                        # data: 以降のJSON部分を取り出す
                        chunk = json.loads(decoded_line[6:])
                        
                        # Difyから新しい会話IDが届いたら保存する
                        if "conversation_id" in chunk:
                            st.session_state.conversation_id = chunk["conversation_id"]
                        
                        # 回答の一部があれば表示
                        if "answer" in chunk:
                            full_response += chunk["answer"]
                            response_placeholder.markdown(full_response + "▌")

            response_placeholder.markdown(full_response)
            st.session_state.messages.append({"role": "assistant", "content": full_response})
