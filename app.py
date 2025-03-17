from flask import Flask, render_template, request, redirect, url_for, jsonify, flash, session
import sqlite3
import os
import pandas as pd
from datetime import datetime
from flask import make_response
import threading
import time

app = Flask(__name__)
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.secret_key = 'your_secret_key'  # フラッシュメッセージに必要

DB_NAME = "database.db" # 実際のデータベースファイル名を指定

def get_user_name_from_device(device_id):
    # 仮のDB操作: デバイスIDで担当者を取得
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM users WHERE device_id = ?", (device_id,))
        result = cursor.fetchone()
        return result[0] if result else "未登録のユーザー"

# ルートでデバイスごとに分岐
# メニュー画面
@app.route('/')
def index():
    user_agent = request.headers.get('User-Agent', '').lower()

    # PCかスマホかを判別
    if 'mobile' in user_agent or 'android' in user_agent or 'iphone' in user_agent:
        return redirect('/mobile')  # スマホ用画面にリダイレクト
    else:
        return redirect('/pc-menu')  # PC用画面にリダイレクト

# Render のスリープを防ぐための `keep-alive` エンドポイント
@app.route('/keep-alive')
def keep_alive_route():
    return jsonify({"message": "Keep-alive ping received"})

# スリープ防止のため、定期的に自身の `/keep-alive` にアクセスする関数
def keep_alive():
    while True:
        try:
            response = app.test_client().get("/keep-alive")  # 自分自身のエンドポイントにアクセス
            print("Keep Alive Ping:", response.status_code)
        except Exception as e:
            print("Keep Alive Error:", e)
        time.sleep(600)  # 10分ごとにリクエストを送信

# サーバー起動時にスリープ防止スレッドを開始
threading.Thread(target=keep_alive, daemon=True).start()

# スマホ用画面
@app.route('/mobile', methods=['GET', 'POST'])
def mobile():
    if request.method == 'POST':
        # 選択された担当者名を取得
        selected_manager = request.form['manager_name']
        return redirect(url_for('mobile_work_screen', manager_name=selected_manager))

    # DBから担当者情報を取得
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 担当者No, 担当者名 FROM table_manager_master")
        manager_data = cursor.fetchall()

    # スマホ用作業画面を表示
    return render_template('mobile_screen.html', manager_data=manager_data)

# スマホ用作業画面のルート
@app.route('/mobile-work-screen/<manager_name>', methods=['GET'])
def mobile_work_screen(manager_name):
    current_date = datetime.now().strftime("%Y-%m-%d")
    task = None

    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        # 担当者名に一致する「優先順位が高い」「ステータスが未実施」「取込日が今日」のデータを1件取得
        cursor.execute("""
            SELECT 車No, 車名, 工程
            FROM work_table
            WHERE 担当者名 = ? AND ステータス = '未実施' AND 取込日 = ?
            ORDER BY 優先順位 ASC
            LIMIT 1
        """, (manager_name, current_date))
        task = cursor.fetchone()

    return render_template('mobile_work_screen.html', manager_name=manager_name, task=task)

# 作業開始処理
@app.route('/start-task', methods=['POST'])
def start_task():
    data = request.json
    car_no = data['carNo']
    car_name = data['carName']
    process = data['process']
    manager_name = data['managerName']
    current_date = datetime.now().strftime("%Y-%m-%d")

    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        # 作業開始時にステータスを「作業中」に更新し、作業開始日と開始時間を登録
        cursor.execute("""
            UPDATE work_table
            SET ステータス = '作業中',
                作業開始日 = ?,
                開始時間 = ?
            WHERE 車No = ? AND 車名 = ? AND 工程 = ? AND 担当者名 = ? AND ステータス = '未実施' AND 取込日 = ?
        """, (current_date, datetime.now().strftime("%H:%M:%S"),
              car_no, car_name, process, manager_name, current_date))
        conn.commit()

    return jsonify({'status': 'success'})

# 作業中断処理
@app.route('/pause-task', methods=['POST'])
def pause_task():
    data = request.json
    car_no = data['carNo']
    car_name = data['carName']
    process = data['process']
    manager_name = data['managerName']
    current_date = datetime.now().strftime("%Y-%m-%d")

    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()

        # 作業開始日・開始時間を取得
        cursor.execute("""
            SELECT 作業開始日, 開始時間
            FROM work_table
            WHERE 車No = ? AND 車名 = ? AND 工程 = ? AND 担当者名 = ? 
            AND ステータス = '作業中' AND 取込日 = ?
        """, (car_no, car_name, process, manager_name, current_date))
        start_data = cursor.fetchone()

        if start_data:
            start_date, start_time = start_data

            # 作業中断日時を取得
            end_date = current_date
            end_time = datetime.now().strftime("%H:%M:%S")

            # SQLiteで合計時間を計算し、作業終了日・終了時間・合計時間を更新
            cursor.execute("""
                UPDATE work_table
                SET ステータス = '中断中',
                    作業終了日 = ?,
                    終了時間 = ?,
                    合計時間 = COALESCE(合計時間, 0) + 
                        ((julianday(?) - julianday(作業開始日)) * 24 * 60 * 60 +
                        (strftime('%s', ?) - strftime('%s', 開始時間))) / 3600.0
                WHERE 車No = ? AND 車名 = ? AND 工程 = ? AND 担当者名 = ? 
                AND ステータス = '作業中' AND 取込日 = ?
            """, (end_date, end_time, end_date, end_time, car_no, car_name, process, manager_name, current_date))
            conn.commit()

    return jsonify({'status': 'success'})


# 作業再開処理
@app.route('/resume-task', methods=['POST'])
def resume_task():
    data = request.json
    car_no = data['carNo']
    car_name = data['carName']
    process = data['process']
    manager_name = data['managerName']
    current_date = datetime.now().strftime("%Y-%m-%d")

    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()

        # 作業再開時にステータスを「作業中」に更新し、作業開始日と開始時間を登録
        cursor.execute("""
            UPDATE work_table
            SET ステータス = '作業中',
                作業開始日 = ?,
                開始時間 = ?
            WHERE 車No = ? AND 車名 = ? AND 工程 = ? AND 担当者名 = ? AND ステータス = '中断中' AND 取込日 = ?
        """, (current_date, datetime.now().strftime("%H:%M:%S"),
              car_no, car_name, process, manager_name, current_date))
        conn.commit()

    return jsonify({'status': 'success'})

# 作業終了処理
@app.route('/finish-task', methods=['POST'])
def finish_task():
    data = request.json
    car_no = data['carNo']
    car_name = data['carName']
    process = data['process']
    manager_name = data['managerName']
    current_date = datetime.now().strftime("%Y-%m-%d")

    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()

        # 作業開始日・開始時間を取得
        cursor.execute("""
            SELECT ステータス, 作業開始日, 開始時間
            FROM work_table
            WHERE 車No = ? AND 車名 = ? AND 工程 = ? AND 担当者名 = ? 
            AND (ステータス = '作業中' OR ステータス = '中断中') AND 取込日 = ?
        """, (car_no, car_name, process, manager_name, current_date))
        task_data = cursor.fetchone()

        if task_data:
            status, start_date, start_time = task_data
            end_date = current_date
            end_time = datetime.now().strftime("%H:%M:%S")
            
            if status == '作業中':
                # 作業終了時にステータスを「終了」に更新し、作業終了日・終了時間・合計時間を登録。クエリで計算済みの合計時間を登録
                cursor.execute("""
                    UPDATE work_table
                    SET ステータス = '終了',
                        作業終了日 = ?,
                        終了時間 = ?,
                        合計時間 = COALESCE(合計時間, 0) + 
                            ((julianday(?) - julianday(作業開始日)) * 24 * 60 * 60 +
                            (strftime('%s', ?) - strftime('%s', 開始時間))) / 3600.0
                    WHERE 車No = ? AND 車名 = ? AND 工程 = ? AND 担当者名 = ? 
                    AND ステータス = '作業中' AND 取込日 = ?
                """, (end_date, end_time, end_date, end_time, 
                      car_no, car_name, process, manager_name, current_date))
            else:
                cursor.execute("""
                    UPDATE work_table
                    SET ステータス = '終了'
                    WHERE 車No = ? AND 車名 = ? AND 工程 = ? AND 担当者名 = ? 
                    AND ステータス = '中断中' AND 取込日 = ?
                """, (car_no, car_name, process, manager_name, current_date))
            
            conn.commit()

    # 作業が終了したので、新しい作業を取得
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 車No, 車名, 工程
            FROM work_table
            WHERE 担当者名 = ? AND ステータス = '未実施' AND 取込日 = ?
            ORDER BY 優先順位 ASC
            LIMIT 1
        """, (manager_name, current_date))
        next_task = cursor.fetchone()

    return jsonify({'status': 'success', 'nextTask': next_task})







# PC用画面のルート
@app.route('/pc-menu', methods=['GET'])
def pc_menu():
    return render_template('menu.html')  # メニュー画面のテンプレート
    
# データ取込画面より、excelで読み込んだ中身の情報をテーブルとして登録する関数
def insert_data_into_work_table(df):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        # データを挿入
        for _, row in df.iterrows():
            cursor.execute("""
                INSERT INTO work_table (
                    車No, 車名, 工程, ステータス, 作業開始日, 作業終了日, 開始時間, 終了時間, 合計時間, 入庫日, 担当者名, 優先順位
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, tuple(row))
        conn.commit()


# データ取込画面
@app.route('/data-import', methods=['GET', 'POST'])
def upload():
    if request.method == "POST":
        if "file" not in request.files:
            flash("ファイルが選択されていません。")
            return redirect(request.url)

        # 念のためのチェック
        file = request.files["file"]
        if file.filename == "":
            flash("有効なファイルを選択してください。")
            return redirect(request.url)

        if file and file.filename.endswith(".xlsx"):
            try:
                # Excelデータの読み込み
                df = pd.read_excel(file)

                # SQLiteテーブル(work_table)にデータを挿入
                insert_data_into_work_table(df)
                # 登録完了画面にリダイレクト
                return render_template("success.html", message=f"データが正常に登録されました。")
            
            except Exception as e:
                flash(f"DBへの登録に失敗しました、入力している情報に誤りがないかを確認のうえ、再実行してください: {e}")
                return redirect(request.url)
        else:
            flash("Excelファイル（.xlsx）が添付されていません。")
            return redirect(request.url)
    return render_template("data-import.html")

# データ検索画面
@app.route('/data-search', methods=['GET', 'POST'])
def search():
    selected_date = None
    error_message = None

    if request.method == 'POST':
        selected_date = request.form['date']
        if not selected_date:
            error_message = "日付を入力してください。"
        else:
            try:
                with sqlite3.connect(DB_NAME) as conn:
                    cursor = conn.cursor()
                    # 指定した日付が work_table に存在するか確認
                    cursor.execute("SELECT COUNT(*) FROM work_table WHERE 取込日 = ?", (selected_date,))
                    result = cursor.fetchone()
                    if result[0] > 0:
                        # データが存在する場合、結果画面にリダイレクト
                        return redirect(url_for('data_result', search_date=selected_date))
                    else:
                        error_message = "選択したデータがありません。"
            except Exception as e:
                error_message = f"エラーが発生しました: {e}"

    return render_template('search.html', selected_date=selected_date, error_message=error_message)

# 検索結果画面のルート
@app.route('/data-result', methods=['GET', 'POST'])
def data_result():
    search_date = request.args.get('search_date')  # クエリパラメータから検索日付を取得
    if not search_date:
        flash("検索日付が指定されていません。", "danger")
        return redirect(url_for('search'))

    try:
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            # 指定した日付のデータを work_table から取得
            cursor.execute("""
                SELECT rowid, 車No, 車名, 工程, ステータス, 作業開始日, 作業終了日, 合計時間, 入庫日, 担当者名, 優先順位
                FROM work_table WHERE 取込日 = ?
            """, (search_date,))
            table_data = cursor.fetchall()
            columns = ["No", "車No", "車名", "工程", "ステータス", "作業開始日", "作業終了日", "合計時間", "入庫日", "担当者名", "優先順位"]

        if not table_data:
            flash("指定した日付のデータが見つかりませんでした。", "danger")
            return redirect(url_for('search'))

        # No を付番（検索結果の順に1から付ける）
        table_data = [[index + 1] + list(row) for index, row in enumerate(table_data)]

    except Exception as e:
        flash(f"エラーが発生しました: {e}", "danger")
        return redirect(url_for('search'))

    return render_template('data_result.html', display_date=search_date, table_data=table_data, columns=columns)


# 行追加時の ID 取得 API
@app.route('/get-next-id', methods=['GET'])
def get_next_id():
    search_date = request.args.get('search_date')  # クエリパラメータから検索日付を取得
    
    if not search_date:
        return jsonify({'status': 'error', 'message': '検索日付が指定されていません。'})

    try:
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            # 最大 No を取得
            cursor.execute("SELECT MAX(rowid) FROM work_table WHERE 取込日 = ?", (search_date,))
            max_id = cursor.fetchone()[0] or 0  # NULL の場合は 0 を設定

        return jsonify({'status': 'success', 'next_id': max_id + 1})
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'IDの取得に失敗しました: {e}'})


# 行の更新・新規追加処理
@app.route('/update-row', methods=['POST'])
def update_row():
    data = request.json
    row_id = data.get('row_id')  # 既存行の場合 row_id がある
    row_data = data.get('row_data')
    search_date = data.get('search_date')
    required_fields = ["車No", "車名", "工程", "ステータス", "合計時間", "入庫日", "担当者名", "優先順位"]

    # 必須項目のチェック
    for field in required_fields:
        if field not in row_data or not row_data[field]:
            return jsonify({'status': 'error', 'message': "新規追加内容が誤っているため、登録ができませんでした。"})

    try:
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()

            # `合計時間` を float に変換（None の場合は 0.00）
            row_data["合計時間"] = float(row_data["合計時間"]) if row_data["合計時間"] else 0.00

            if row_id:
                # 更新処理（型チェックも含む）
                cursor.execute("SELECT rowid FROM work_table WHERE rowid = ?", (row_id,))
                existing_row = cursor.fetchone()
                if not existing_row:
                    return jsonify({'status': 'error', 'message': "編集内容が誤っているため、変更ができませんでした。"})

                update_query = """
                    UPDATE work_table SET 車No = ?, 車名 = ?, 工程 = ?, ステータス = ?, 作業開始日 = ?, 作業終了日 = ?, 合計時間 = ?, 入庫日 = ?, 担当者名 = ?, 優先順位 = ?
                    WHERE rowid = ?
                """
                cursor.execute(update_query, (
                    row_data["車No"], row_data["車名"], row_data["工程"], row_data["ステータス"],
                    row_data.get("作業開始日"), row_data.get("作業終了日"), row_data["合計時間"],
                    row_data["入庫日"], row_data["担当者名"], row_data["優先順位"], row_id
                ))

            else:
                # `開始時間` と `終了時間` は空文字 or NULL を設定
                insert_query = """
                    INSERT INTO work_table (車No, 車名, 工程, ステータス, 作業開始日, 作業終了日, 開始時間, 終了時間, 合計時間, 入庫日, 担当者名, 優先順位, 取込日)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """

                # `開始時間` `終了時間` を自動で `""` にしてデータ数を合わせる
                insert_values = [
                    row_data["車No"], row_data["車名"], row_data["工程"], row_data["ステータス"],
                    row_data.get("作業開始日"), row_data.get("作業終了日"), "", "",
                    row_data["合計時間"], row_data["入庫日"], row_data["担当者名"], row_data["優先順位"],
                    search_date  # 取込日を追加
                ]
                cursor.execute(insert_query, insert_values)

            conn.commit()
        return jsonify({'status': 'success'})
    except ValueError:
        return jsonify({'status': 'error', 'message': "数値の型が誤っています。整数または小数で入力してください。"})
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'エラーが発生しました: {e}'})





# 行の削除処理
@app.route('/delete-row', methods=['POST'])
def delete_row():
    data = request.json
    row_id = data.get('row_id')

    if not row_id:
        return jsonify({'status': 'error', 'message': "削除対象の行が選択されていません。"})

    try:
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM work_table WHERE rowid = ?", (row_id,))
            conn.commit()

        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'削除に失敗しました: {e}'})

# 読み取り専用のデータ結果表示
def get_data_results_readonly(search_date):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 車No, 車名, 工程, ステータス, 作業開始日, 作業終了日, 合計時間, 入庫日, 担当者名, 優先順位
            FROM work_table WHERE 取込日 = ?
        """, (search_date,))
        return cursor.fetchall()

@app.route('/data-result-readonly', methods=['GET'])
def data_result_readonly():
    search_date = request.args.get('search_date')
    if not search_date:
        flash("検索日付が指定されていません。", "danger")
        return redirect(url_for('search'))

    try:
        table_data = get_data_results_readonly(search_date)
        columns = ["No", "車No", "車名", "工程", "ステータス", "作業開始日", "作業終了日", "合計時間", "入庫日", "担当者名", "優先順位"]
        if not table_data:
            flash("指定した日付のデータが見つかりませんでした。", "danger")
            return redirect(url_for('search'))
        table_data = [[index + 1] + list(row) for index, row in enumerate(table_data)]
    except Exception as e:
        flash(f"エラーが発生しました: {e}", "danger")
        return redirect(url_for('search'))

    return render_template('data_result_readonly.html', display_date=search_date, table_data=table_data, columns=columns)

#検索結果画面(作業内容画面)-本日の作業終了ボタン
@app.route('/end-work', methods=['POST'])
def end_work():
    return redirect(url_for('index'))


# 月次集計 - データベースから利用可能な月を取得する関数
def get_available_months():
    months = set()  # 月情報を格納するセット（重複を避けるため）
    try:
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT strftime('%Y-%m', 取込日) FROM work_table ORDER BY 取込日 ASC")
            months = {row[0] for row in cursor.fetchall() if row[0]}
    except Exception as e:
        print(f"Error fetching months: {e}")  # エラーメッセージを表示
    return sorted(months)  # 昇順にソートしてリストに変換

# 月次集計 - 期間選択画面
@app.route('/monthly-summary', methods=['GET', 'POST'])
def monthly_summary():
    available_months = get_available_months()  # 利用可能な月を取得
    selected_month = None
    error_message = None

    if request.method == 'POST':
        selected_month = request.form.get('month', '')  # 入力された月を取得
        if not selected_month:
            error_message = "月を選択してください。"
        else:
            return redirect(url_for('monthly_summary_unit_selection', selected_month=selected_month))

    return render_template('monthly-summary.html', available_months=available_months, selected_month=selected_month, error_message=error_message)

# 月次集計 - 単位選択画面
@app.route('/monthly-summary-unit-selection', methods=['GET', 'POST'])
def monthly_summary_unit_selection():
    selected_month = request.form.get('selected_month', '')  # POSTで受け取る
    if not selected_month:
        selected_month = request.args.get('selected_month', '')  # GET も確認
    if not selected_month:
        return "エラー: 期間が指定されていません", 400  # エラーハンドリング

    if request.method == 'POST':
        unit = request.form['unit']
        if unit == "車単位":
            results = aggregate_by_car(selected_month)
        elif unit == "工程単位":
            results = aggregate_by_process(selected_month)
        else:
            results = aggregate_by_car_process(selected_month)
        session['results'] = results  # セッションに保存
        return redirect(url_for('monthly_summary_results', selected_month=selected_month, unit=unit))

    return render_template('monthly-summary-unit-selection.html', selected_month=selected_month)


# 集計処理: 車単位の集計
def aggregate_by_car(selected_month):
    aggregated_data = {}
    try:
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 車No, 車名, COALESCE(合計時間, 0) FROM work_table WHERE strftime('%Y-%m', 取込日) = ?", (selected_month,))
            rows = cursor.fetchall()
            for row in rows:
                key = (row[0], row[1])  # 車No, 車名をキーにする
                time_value = float(row[2]) if row[2] else 0.0  # 数値型に変換
                aggregated_data[key] = aggregated_data.get(key, 0.0) + time_value  # 合計時間を加算
    except Exception as e:
        print(f"Error during car aggregation: {e}")

    # Noを追加してリスト化
    aggregated_list = [[i+1] + list(k) + [v] for i, (k, v) in enumerate(aggregated_data.items())]
    return aggregated_list

# 集計処理: 工程単位の集計
def aggregate_by_process(selected_month):
    aggregated_data = {}
    try:
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 車No, 車名, 工程, COALESCE(合計時間, 0) FROM work_table WHERE strftime('%Y-%m', 取込日) = ?", (selected_month,))
            rows = cursor.fetchall()
            for row in rows:
                key = (row[0], row[1], row[2])  # 車No, 車名, 工程をキーにする
                time_value = float(row[3]) if row[3] else 0.0  # 数値型に変換
                aggregated_data[key] = aggregated_data.get(key, 0.0) + time_value  # 合計時間を加算
    except Exception as e:
        print(f"Error during process aggregation: {e}")

    # Noを追加してリスト化
    aggregated_list = [[i+1] + list(k) + [v] for i, (k, v) in enumerate(aggregated_data.items())]
    return aggregated_list

# 集計処理: 車・工程単位の集計
def aggregate_by_car_process(selected_month):
    aggregated_data = {}
    try:
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 車No, 車名, 工程, 担当者名, COALESCE(合計時間, 0) FROM work_table WHERE strftime('%Y-%m', 取込日) = ?", (selected_month,))
            rows = cursor.fetchall()
            for row in rows:
                key = (row[0], row[1], row[2], row[3])  # 車No, 車名, 工程, 担当者名をキーにする
                time_value = float(row[4]) if row[4] else 0.0  # 数値型に変換
                aggregated_data[key] = aggregated_data.get(key, 0.0) + time_value  # 合計時間を加算
    except Exception as e:
        print(f"Error during car-process aggregation: {e}")

    # Noを追加してリスト化（入庫日を除外）
    aggregated_list = [[i+1] + list(k) + [v] for i, (k, v) in enumerate(aggregated_data.items())]
    return aggregated_list



# 月次集計結果画面 - 集計データ表示
@app.route('/monthly-summary-results', methods=['GET', 'POST'])
def monthly_summary_results():
    selected_month = request.args.get('selected_month')
    unit = request.args.get('unit')
    results = session.get('results', [])  # セッションから取得

    if request.method == 'POST':
        action = request.form.get('action')
        if action == "戻る":
            return redirect(url_for('monthly_summary_unit_selection', selected_month=selected_month))
        return redirect(url_for('pc_menu'))  # メニュー画面に戻る

    return render_template('monthly-summary-results.html', selected_month=selected_month, unit=unit, results=results)

# 作業者管理画面のルート
def get_managers():
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 担当者No, 担当者名 FROM table_manager_master ORDER BY 担当者No ASC")
        return cursor.fetchall()

@app.route('/manage-managers', methods=['GET'])
def manage_managers():
    managers = get_managers()
    return render_template('manager_management.html', managers=managers)

# 作業者の登録処理
@app.route('/register-manager', methods=['POST'])
def register_manager():
    try:
        manager_name = request.form['manager_name']
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO table_manager_master (担当者名) VALUES (?)", (manager_name,))
            conn.commit()
        return jsonify({'status': 'success', 'message': "作業者が登録されました！"})
    except Exception as e:
        return jsonify({'status': 'error', 'message': f"エラーが発生しました: {str(e)}"})

# 作業者の更新処理
@app.route('/update-manager', methods=['POST'])
def update_manager():
    try:
        data = request.json
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE table_manager_master SET 担当者名 = ? WHERE 担当者No = ?", (data['name'], data['id']))
            conn.commit()
        return jsonify({'status': 'success', 'message': "作業者情報を更新しました！"})
    except Exception as e:
        return jsonify({'status': 'error', 'message': f"エラーが発生しました: {str(e)}"})

# 作業者の削除処理
@app.route('/delete-manager', methods=['POST'])
def delete_manager():
    try:
        data = request.json
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM table_manager_master WHERE 担当者No = ?", (data['id'],))
            conn.commit()
        return jsonify({'status': 'success', 'message': "作業者を削除しました！"})
    except Exception as e:
        return jsonify({'status': 'error', 'message': f"エラーが発生しました: {str(e)}"})

if __name__ == '__main__':
    app.run(debug=True)
