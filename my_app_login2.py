import streamlit as st
import streamlit_authenticator as stauth
import requests
import json
from openai import OpenAI
from streamlit_mic_recorder import mic_recorder
import io
from streamlit_gsheets import GSheetsConnection
import datetime
import pandas as pd

# --- 1. ユーザー情報の設定 ---
names = ["田中 太郎", "佐藤 花子"]
usernames = ["tanaka", "sato"]
passwords = ["pass123", "pass456"]

authenticator = stauth.Authenticate(
    {'usernames': {
        usernames[0]: {'name': names[0], 'password': passwords[0]},
        usernames[1]: {'name': names[1], 'password': passwords[1]}
    }},
    "dify_app_cookie", "signature_key", cookie_expiry_days=30
)

# --- 2. ログイン画面 ---
authenticator.login('main')

if st.session_state["authentication_status"]:
    username = st.session_state["username"]
    name = st.session_state["name"]
    client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])
    conn = st.connection("gsheets", type=GSheetsConnection)

    with st.sidebar:
        st.write(f"ようこそ、{name} さん")
        authenticator.logout('ログアウト', 'sidebar')

    st.title("音声対応AIアシスタント")

    # セッション状態の初期化
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "conversation_id" not in st.session_state:
        st.session_state.conversation_id = ""
    if "first_run" not in st.session_state:
        st.session_state.first_run = True

    DIFY_KEY = st.secrets["DIFY_API_KEY"]
    headers = {"Authorization": f"Bearer {DIFY_KEY}", "Content-Type": "application/json"}

    # --- 初回ログイン時の「開始メッセージ」取得ロジック ---
    if st.session_state.first_run:
        try:
            # Difyのパラメータから開始メッセージを取得
            params_res = requests.get("https://api.dify.ai/v1/parameters", headers=headers, params={"user": username})
            params_res.raise_for_status()
            params_data = params_res.json()
            
            # opening_statementを取得（空の場合はデフォルト値を設定）
            opening_statement = params_data.get("opening_statement", "")
            
            if opening_statement:
                st.session_state.messages.append({"role": "assistant", "content": opening_statement})
                
                # ★修正ポイント: 文字列が空でないことを確認してからTTSを実行
                if len(opening_statement.strip()) > 0:
                    tts_init = client.audio.speech.create(model="tts-1", voice="alloy", input=opening_statement)
                    st.audio(io.BytesIO(tts_init.content), format="audio/mp3", autoplay=True)
            else:
                # Dify側で「開始の挨拶」が未設定の場合のフォールバック
                fallback_msg = "こんにちは！何かお手伝いしましょうか？"
                st.session_state.messages.append({"role": "assistant", "content": fallback_msg})
                tts_init = client.audio.speech.create(model="tts-1", voice="alloy", input=fallback_msg)
                st.audio(io.BytesIO(tts_init.content), format="audio/mp3", autoplay=True)
                
        except Exception as e:
            st.error(f"初期設定取得エラー: {e}")
        
        st.session_state.first_run = False
        st.rerun()

    # --- UI ---
    st.write("話しかけてください：")
    audio = mic_recorder(start_prompt="⏺️ 録音開始", stop_prompt="⏹️ 停止", key='recorder')
    user_input = None

    if audio:
        audio_bio = io.BytesIO(audio['bytes'])
        audio_bio.name = "audio.wav"
        with st.spinner('音声を解析中...'):
            transcript = client.audio.transcriptions.create(model="whisper-1", file=audio_bio)
            user_input = transcript.text
    
    chat_input = st.chat_input("またはメッセージを入力...")
    if chat_input:
        user_input = chat_input

    # 履歴表示
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    # --- メインチャット処理 ---
    if user_input:
        st.session_state.messages.append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        with st.chat_message("assistant"):
            response_placeholder = st.empty()
            full_response = ""

            data = {
                "inputs": {}, "query": user_input, "response_mode": "streaming",
                "user": username, "conversation_id": st.session_state.conversation_id
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

            # スプレッドシートへログ保存
            try:
                now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S')
                existing_data = conn.read(spreadsheet=st.secrets["spreadsheet_url"], ttl=0)
                new_row = pd.DataFrame([{
                    "date": now, "user_id": username, "user_input": user_input,
                    "ai_response": full_response, "conversation_id": st.session_state.conversation_id
                }])
                updated_df = pd.concat([existing_data, new_row], ignore_index=True)
                conn.update(spreadsheet=st.secrets["spreadsheet_url"], data=updated_df)
            except Exception as e:
                st.warning(f"ログ保存に失敗しましたが、対話は継続できます: {e}")

            # ★修正ポイント: 応答が空でないことを確認してから音声生成
            if len(full_response.strip()) > 0:
                with st.spinner('音声を生成中...'):
                    tts_res = client.audio.speech.create(model="tts-1", voice="alloy", input=full_response)
                    st.audio(io.BytesIO(tts_res.content), format="audio/mp3", autoplay=True)

elif st.session_state["authentication_status"] is False:
    st.error('ユーザー名またはパスワードが間違っています')
