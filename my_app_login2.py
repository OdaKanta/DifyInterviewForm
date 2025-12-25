import streamlit as st
import streamlit_authenticator as stauth
import requests
import json
from openai import OpenAI
from streamlit_mic_recorder import mic_recorder
import io
import yaml # 設定保存用
from streamlit_gsheets import GSheetsConnection
import datetime

# --- 1. ユーザー情報の設定 ---
names = ["田中 太郎", "佐藤 花子", "工大 太郎", "工大 花子"]
usernames = ["tanaka", "sato", "kodai", "hanako"]
passwords = ["pass123", "pass456", "password", "password"]

# ログイン部品の準備
authenticator = stauth.Authenticate(
    {'usernames': {
        usernames[0]: {'name': names[0], 'password': passwords[0]},
        usernames[1]: {'name': names[1], 'password': passwords[1]},
        usernames[2]: {'name': names[2], 'password': passwords[2]}
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
    client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])
    conn = st.connection("gsheets", type=GSheetsConnection)

    # 会話履歴と会話IDの初期化
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "conversation_id" not in st.session_state:
        st.session_state.conversation_id = ""

    # --- ★追加：ログイン直後の「最初の挨拶」を取得する処理 ---
    # 履歴が空のとき、自動的にDifyへ「開始」をリクエストする
    if len(st.session_state.messages) == 0:
        with st.chat_message("assistant"):
            response_placeholder = st.empty()
            full_response = ""
            
            DIFY_KEY = st.secrets["DIFY_API_KEY"]
            headers = {"Authorization": f"Bearer {DIFY_KEY}", "Content-Type": "application/json"}
            
            # 最初の挨拶を取得するために「inputs」は空、
            # queryには「(開始)」などのダミーを入れるか、Dify側で設定されていれば空でも動きます
            data = {
                "inputs": {},
                "query": "こんにちは", # Dify側で会話を開始させるためのトリガー
                "response_mode": "streaming",
                "user": username
            }

            response = requests.post("https://api.dify.ai/v1/chat-messages", headers=headers, json=data, stream=True)

            for line in response.iter_lines():
                if line:
                    decoded_line = line.decode('utf-8')
                    if decoded_line.startswith('data: '):
                        chunk = json.loads(decoded_line[6:])
                        if "conversation_id" in chunk:
                            st.session_state.conversation_id = chunk["conversation_id"]
                        
                        # チャットボット形式
                        if "answer" in chunk:
                            full_response += chunk["answer"]
                            response_placeholder.markdown(full_response + "▌")
                        # チャットフロー形式（text_chunk）
                        elif "event" in chunk and chunk["event"] == "text_chunk":
                            if "data" in chunk and "text" in chunk["data"]:
                                full_response += chunk["data"]["text"]
                                response_placeholder.markdown(full_response + "▌")

            response_placeholder.markdown(full_response)
            st.session_state.messages.append({"role": "assistant", "content": full_response})
            
            # 最初の挨拶も音声で再生
            if full_response.strip():
                tts_response = client.audio.speech.create(model="tts-1", voice="alloy", input=full_response)
                st.audio(io.BytesIO(tts_response.content), format="audio/mp3", autoplay=True)

    # --- 過去のメッセージを表示 ---
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    # --- メイン処理 (Dify送信 & ログ保存) ---
    if user_input:
        st.session_state.messages.append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        with st.chat_message("assistant"):
            response_placeholder = st.empty()
            full_response = ""

            DIFY_KEY = st.secrets["DIFY_API_KEY"]
            headers = {"Authorization": f"Bearer {DIFY_KEY}", "Content-Type": "application/json"}
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

            # --- ★ここからログ書き込み処理 ---
            try:
                # 現在の時刻
                now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S')
                
                # 【重要】ttl=0 を追加してキャッシュを無効化し、常に最新のスプレッドシートを読み込む
                # また、空の行を読み込まないように引数を調整
                existing_data = conn.read(
                    spreadsheet=st.secrets["spreadsheet_url"], 
                    ttl=0  # キャッシュを0秒にする（毎回新しく読み込む）
                )
                
                # 新しい行を作成
                new_row = {
                    "date": now,
                    "user_id": username,
                    "user_input": user_input,
                    "ai_response": full_response,
                    "conversation_id": st.session_state.conversation_id
                }
                
                # データを追記
                import pandas as pd
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

            # --- 音声出力 (OpenAI TTS) ---
            # full_response が空（""）でないか、また文字数が少なすぎないか確認
            if full_response.strip(): 
                with st.spinner('音声を生成中...'):
                    try:
                        tts_response = client.audio.speech.create(
                            model="tts-1", 
                            voice="alloy", 
                            input=full_response
                        )
                        st.audio(io.BytesIO(tts_response.content), format="audio/mp3", autoplay=True)
                    except Exception as e:
                        st.error(f"音声生成エラー: {e}")
            else:
                st.warning("AIからの回答が空だったため、音声は生成されませんでした。")
