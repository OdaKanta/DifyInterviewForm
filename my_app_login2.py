import streamlit as st
import streamlit_authenticator as stauth
import requests
import json
from openai import OpenAI
from streamlit_mic_recorder import mic_recorder
import io
import datetime
import pandas as pd
from streamlit_gsheets import GSheetsConnection

# --- 1. ユーザー情報の設定 ---
names = ["田中 太郎", "佐藤 花子", "工大 太郎"]
usernames = ["tanaka", "sato", "kodai"]
passwords = ["pass123", "pass456", "password"]

authenticator = stauth.Authenticate(
    {'usernames': {
        usernames[0]: {'name': names[0], 'password': passwords[0]},
        usernames[1]: {'name': names[1], 'password': passwords[1]},
        usernames[2]: {'name': names[2], 'password': passwords[2]}
    }},
    "dify_app_cookie",
    "signature_key",
    cookie_expiry_days=30
)

authenticator.login('main')

if st.session_state["authentication_status"]:
    username = st.session_state["username"]
    name = st.session_state["name"]

    client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])
    conn = st.connection("gsheets", type=GSheetsConnection)

    with st.sidebar:
        st.write(f"ようこそ、{name} さん")
        authenticator.logout('ログアウト', 'sidebar')

    st.title("音声対応AIアシスタント 1")

    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "conversation_id" not in st.session_state:
        st.session_state.conversation_id = ""

    # --- 入力 UI ---
    st.write("話しかけてください：")
    audio = mic_recorder(
        start_prompt="⏺️ 録音開始",
        stop_prompt="⏹️ 停止",
        key='recorder'
    )

    user_input = None

    if audio:
        audio_bio = io.BytesIO(audio['bytes'])
        audio_bio.name = "audio.wav"
        with st.spinner('音声を解析中...'):
            try:
                transcript = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_bio
                )
                user_input = transcript.text
            except Exception as e:
                st.error(f"音声認識エラー: {e}")

    chat_input = st.chat_input("またはメッセージを入力...")
    if chat_input:
        user_input = chat_input

    # --- 履歴表示 ---
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    # --- メイン処理 ---
    if user_input:
        st.session_state.messages.append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        with st.chat_message("assistant"):
            response_placeholder = st.empty()
            full_response = ""

            headers = {
                "Authorization": f"Bearer {st.secrets['DIFY_API_KEY']}",
                "Content-Type": "application/json"
            }

            # ★★★★★ 重要修正点 ★★★★★
            data = {
                "inputs": {},
                "query": user_input,
                "response_mode": "streaming",
                "user": username,
                "files": []   # ← 必須
            }

            if st.session_state.conversation_id:
                data["conversation_id"] = st.session_state.conversation_id

            try:
                response = requests.post(
                    "https://api.dify.ai/v1/chat-messages",
                    headers=headers,
                    json=data,
                    stream=True,
                    timeout=60
                )

                if response.status_code != 200:
                    st.error(f"Dify API Error {response.status_code}: {response.text}")
                else:
                    for line in response.iter_lines():
                        if not line:
                            continue

                        decoded = line.decode("utf-8").strip()
                        if not decoded.startswith("data:"):
                            continue

                        try:
                            payload = json.loads(decoded[5:].strip())
                            event = payload.get("event")

                            if "conversation_id" in payload:
                                st.session_state.conversation_id = payload["conversation_id"]

                            if event in ("message", "text_chunk"):
                                text = payload.get("answer") or payload.get("data", {}).get("text", "")
                                full_response += text
                                response_placeholder.markdown(full_response + "▌")

                            if event == "error":
                                st.error(payload.get("message"))
                                break

                        except json.JSONDecodeError:
                            continue

                    response_placeholder.markdown(full_response)
                    st.session_state.messages.append(
                        {"role": "assistant", "content": full_response}
                    )

            except Exception as e:
                st.error(f"接続エラー: {e}")

            # --- ログ保存 ---
            if full_response:
                try:
                    now = datetime.datetime.now(
                        datetime.timezone(datetime.timedelta(hours=9))
                    ).strftime('%Y-%m-%d %H:%M:%S')

                    df = conn.read(
                        spreadsheet=st.secrets["spreadsheet_url"],
                        ttl=0
                    )

                    new = pd.DataFrame([{
                        "date": now,
                        "user_id": username,
                        "user_input": user_input,
                        "ai_response": full_response,
                        "conversation_id": st.session_state.conversation_id
                    }])

                    conn.update(
                        spreadsheet=st.secrets["spreadsheet_url"],
                        data=pd.concat([df, new], ignore_index=True)
                    )
                except:
                    pass

                # --- 音声出力 ---
                try:
                    tts = client.audio.speech.create(
                        model="tts-1",
                        voice="alloy",
                        input=full_response[:4000]
                    )
                    st.audio(io.BytesIO(tts.content), autoplay=True)
                except Exception as e:
                    st.error(f"音声生成エラー: {e}")

elif st.session_state["authentication_status"] is False:
    st.error('ユーザー名またはパスワードが間違っています')
elif st.session_state["authentication_status"] is None:
    st.warning('ユーザー名とパスワードを入力してください')
