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

    "dify_app_cookie", # クッキー名

    "signature_key",   # 署名キー

    cookie_expiry_days=30

)



# --- 追加: Dify の開始メッセージを取得する関数 ---
def get_dify_opening_message(username):
    DIFY_KEY = st.secrets["DIFY_API_KEY"]
    headers = {"Authorization": f"Bearer {DIFY_KEY}"}
    # アプリケーション設定を取得するAPI
    params = {"user": username}
    try:
        response = requests.get("https://api.dify.ai/v1/parameters", headers=headers, params=params)
        if response.status_code == 200:
            data = response.json()
            return data.get("opening_statement", "")
    except Exception as e:
        st.error(f"開始メッセージの取得に失敗しました: {e}")
    return ""

# --- 2. ログイン画面の表示 ---
authenticator.login('main')

if st.session_state["authentication_status"]:
    username = st.session_state["username"]
    name = st.session_state["name"]
    client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])

    # --- セッション状態の初期化 ---
    if "messages" not in st.session_state:
        st.session_state.messages = []
        # ★ここで開始メッセージを取得して追加
        opening_msg = get_dify_opening_message(username)
        if opening_msg:
            st.session_state.messages.append({"role": "assistant", "content": opening_msg})
            # 最初のメッセージも音声で再生したい場合は、ここでTTS処理を呼ぶかフラグを立てる
            st.session_state["first_run"] = True 

    if "conversation_id" not in st.session_state:
        st.session_state.conversation_id = ""

    # (中略: サイドバーやタイトル表示)

    # 過去の表示 (ここで最初のボット発言も表示される)
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    # ★初回ログイン時のみ開始メッセージを自動再生する処理 (任意)
    if st.session_state.get("first_run") and st.session_state.messages:
        first_content = st.session_state.messages[0]["content"]
        try:
            tts_response = client.audio.speech.create(
                model="tts-1", voice="alloy", input=first_content
            )
            st.audio(io.BytesIO(tts_response.content), format="audio/mp3", autoplay=True)
        except:
            pass
        st.session_state["first_run"] = False # 1回再生したらオフにする

    # --- 以降、既存の入力処理 ---



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
