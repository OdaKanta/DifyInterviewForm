import streamlit as st
import streamlit_authenticator as stauth
import requests
import json
from openai import OpenAI
from streamlit_mic_recorder import mic_recorder
import io
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

if st.session_state["authentication_status"]:
    username = st.session_state["username"]
    name = st.session_state["name"]

    # OpenAIクライアントの初期化 (Secretsに設定したキーを使用)
    client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])

    with st.sidebar:
        st.write(f"ようこそ、{name} さん")
        authenticator.logout('ログアウト', 'sidebar')

    st.title("音声対応AIアシスタント")

    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "conversation_id" not in st.session_state:
        st.session_state.conversation_id = ""

    # --- 音声入力セクション ---
    st.write("マイクを押して話しかけてください：")
    audio = mic_recorder(start_prompt="⏺️ 録音開始", stop_prompt="⏹️ 停止", key='recorder')

    user_input = None

    # 音声が録音されたら Whisper でテキスト化
    if audio:
        audio_bio = io.BytesIO(audio['bytes'])
        audio_bio.name = "audio.wav"
        
        with st.spinner('音声を解析中...'):
            transcript = client.audio.transcriptions.create(
                model="whisper-1", 
                file=audio_bio
            )
            user_input = transcript.text
            st.success(f"聞き取り結果: {user_input}")

    # 通常のテキスト入力も受け付ける
    chat_input = st.chat_input("またはメッセージを入力...")
    if chat_input:
        user_input = chat_input

    # 過去のメッセージを表示
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    # --- メイン処理 (Dify送信 & 音声合成) ---
    if user_input:
        st.session_state.messages.append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        with st.chat_message("assistant"):
            response_placeholder = st.empty()
            full_response = ""

            # Dify API設定
            DIFY_KEY = st.secrets["DIFY_API_KEY"]
            headers = {"Authorization": f"Bearer {DIFY_KEY}", "Content-Type": "application/json"}
            data = {
                "inputs": {},
                "query": user_input,
                "response_mode": "streaming",
                "user": username,
                "conversation_id": st.session_state.conversation_id
            }

            response = requests.post("https://api.dify.ai/v1/chat-messages", headers=headers, json=data, stream=True)

            for line in response.iter_lines():
                if line:
                    decoded_line = line.decode('utf-8')
                    if decoded_line.startswith('data: '):
                        chunk = json.loads(decoded_line[6:])
                        if "conversation_id" in chunk:
                            st.session_state.conversation_id = chunk["conversation_id"]
                        if "answer" in chunk:
                            full_response += chunk["answer"]
                            response_placeholder.markdown(full_response + "▌")

            response_placeholder.markdown(full_response)
            st.session_state.messages.append({"role": "assistant", "content": full_response})

            # --- 音声出力 (OpenAI TTS) ---
            with st.spinner('音声を生成中...'):
                tts_response = client.audio.speech.create(
                    model="tts-1",
                    voice="alloy", # 声の種類: alloy, echo, fable, onyx, nova, shimmer
                    input=full_response
                )
                # 音声データを再生
                audio_data = io.BytesIO(tts_response.content)
                st.audio(audio_data, format="audio/mp3", autoplay=True)
