import streamlit as st
import streamlit_authenticator as stauth
import requests
import json
from openai import OpenAI
from streamlit_mic_recorder import mic_recorder
import io
import yaml
from streamlit_gsheets import GSheetsConnection
import datetime
import pandas as pd

# --- 1. ユーザー情報の設定 ---
names = ["田中 太郎", "佐藤 花子", "工大 太郎"]
usernames = ["tanaka", "sato", "kodai"]
passwords = ["pass123", "pass456", "password"]

# ログイン部品の準備
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

# --- 2. ログイン画面の表示 ---
authenticator.login('main')

if st.session_state["authentication_status"]:
    username = st.session_state["username"]
    name = st.session_state["name"]
    client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])

    # --- スプレッドシート接続の準備 ---
    conn = st.connection("gsheets", type=GSheetsConnection)

    with st.sidebar:
        st.write(f"ようこそ、{name} さん")
        authenticator.logout('ログアウト', 'sidebar')

    st.title("音声対応AIアシスタント (Dify Chatflow)")

    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "conversation_id" not in st.session_state:
        st.session_state.conversation_id = ""

    # --- 音声・テキスト入力処理 ---
    st.write("話しかけてください：")
    audio = mic_recorder(start_prompt="⏺️ 録音開始", stop_prompt="⏹️ 停止", key='recorder')
    user_input = None

    if audio:
        audio_bio = io.BytesIO(audio['bytes'])
        audio_bio.name = "audio.wav"
        with st.spinner('音声を解析中...'):
            try:
                transcript = client.audio.transcriptions.create(model="whisper-1", file=audio_bio)
                user_input = transcript.text
            except Exception as e:
                st.error(f"音声認識エラー: {e}")
    
    chat_input = st.chat_input("またはメッセージを入力...")
    if chat_input:
        user_input = chat_input

    # 過去の履歴を表示
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    # --- メイン処理 (Dify送信 & ログ保存) ---
    if user_input:
        # ユーザー入力を履歴に追加
        st.session_state.messages.append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        with st.chat_message("assistant"):
            response_placeholder = st.empty()
            full_response = ""

            DIFY_KEY = st.secrets["DIFY_API_KEY"]
            headers = {"Authorization": f"Bearer {DIFY_KEY}", "Content-Type": "application/json"}
            
            # Chatflow用のペイロード
            data = {
                "inputs": {}, 
                "query": user_input, 
                "response_mode": "streaming",
                "user": username, 
                "conversation_id": st.session_state.conversation_id
            }

            try:
                response = requests.post("https://api.dify.ai/v1/chat-messages", headers=headers, json=data, stream=True)
                response.raise_for_status()

                for line in response.iter_lines():
                    if line:
                        decoded_line = line.decode('utf-8')
                        if decoded_line.startswith('data: '):
                            # JSONとして解析
                            try:
                                chunk = json.loads(decoded_line[6:])
                                event = chunk.get("event")

                                # 会話IDの取得
                                if "conversation_id" in chunk:
                                    st.session_state.conversation_id = chunk["conversation_id"]

                                # Chatflowの場合、回答は 'answer' ではなく 'text_chunk' 内の 'delta' に入ることが多い
                                if event == "text_chunk":
                                    content = chunk.get("data", {}).get("text", "")
                                    full_response += content
                                    response_placeholder.markdown(full_response + "▌")
                                
                                # ワークフロー完了時の最終回答取得（予備）
                                elif event == "workflow_finished" or event == "message_end":
                                    # message_end の場合に answer フィールドがある場合も考慮
                                    if "metadata" in chunk and "usage" in chunk:
                                        pass # 終了処理

                            except json.JSONDecodeError:
                                continue

                response_placeholder.markdown(full_response)
                
                if full_response:
                    st.session_state.messages.append({"role": "assistant", "content": full_response})
                else:
                    # full_responseが空の場合、Difyのログを確認するか、イベント名が違う可能性がある
                    st.error("Difyから回答が得られませんでした。ワークフローの出力設定を確認してください。")

            except Exception as e:
                st.error(f"Dify通信エラー: {e}")

            # --- ログ書き込み処理 ---
            if full_response:
                try:
                    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S')
                    
                    existing_data = conn.read(
                        spreadsheet=st.secrets["spreadsheet_url"], 
                        ttl=0
                    )
                    
                    new_row = {
                        "date": now,
                        "user_id": username,
                        "user_input": user_input,
                        "ai_response": full_response,
                        "conversation_id": st.session_state.conversation_id
                    }
                    
                    new_row_df = pd.DataFrame([new_row])
                    
                    if existing_data.empty:
                        updated_df = new_row_df
                    else:
                        updated_df = pd.concat([existing_data, new_row_df], ignore_index=True)
                    
                    conn.update(spreadsheet=st.secrets["spreadsheet_url"], data=updated_df)
                except Exception as e:
                    st.warning(f"ログ保存に失敗しました: {e}")

            # --- 音声出力 (OpenAI TTS) ---
            if full_response.strip(): 
                with st.spinner('音声を生成中...'):
                    try:
                        tts_response = client.audio.speech.create(
                            model="tts-1", 
                            voice="alloy", 
                            input=full_response[:4096] # TTSの制限対策
                        )
                        st.audio(io.BytesIO(tts_response.content), format="audio/mp3", autoplay=True)
                    except Exception as e:
                        st.error(f"音声生成エラー: {e}")
            else:
                st.warning("AIからの回答が空だったため、音声は生成されませんでした。")
