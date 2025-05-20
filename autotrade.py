"""
비트코인 AI 자동 거래 봇
=========================================================
* 주요 기능:
  - 멀티 타임프레임 분석: 단기(15분), 중기(4시간), 장기(일) 차트 분석
  - 뉴스 감성 분석: 최신 비트코인 뉴스를 수집하고 감성 점수(-10~+10) 산출
  - 자가 학습 기능: 과거 거래 데이터를 분석하여 성공/실패 패턴 학습
  - 켈리 기준(Kelly Criterion) 기반 포지션 사이징: 최적의 투자 비율 결정
  - 동적 레버리지 최적화: 시장 상황에 따라 레버리지(1x-10x) 자동 조절
  - 동적 손절매/익절매 최적화: 최적의 위험:보상 비율 설정
  - 거래 내역 데이터베이스 저장 및 분석

* 개발자: [본인 이름]
* 버전: 1.0.0
* 날짜: 2025-05-20
"""

import os
import math
import time
import pandas as pd
import json
import re
import sqlite3
from datetime import datetime, timedelta

try:
    # 필수 패키지 임포트
    import ccxt  # 암호화폐 거래소 API
    from dotenv import load_dotenv  # 환경 변수 관리
    from openai import OpenAI  # OpenAI API 연동
except ImportError:
    print("필요한 패키지가 설치되어 있지 않습니다. 다음 명령어로 설치하세요:")
    print("pip install ccxt python-dotenv openai pandas")
    exit(1)

# ==========================================================
# 1. 초기 설정 및 환경 변수
# ==========================================================

# .env 파일에서 환경 변수 로드
load_dotenv()

# 거래 설정
SYMBOL = "BTC/USDT"  # 거래 페어 설정
MAX_POSITION_SIZE = 0.2  # 최대 자본의 20% 투자
MIN_LEVERAGE = 1  # 최소 레버리지
MAX_LEVERAGE = 10  # 최대 레버리지

# API 키 설정
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_SECRET_KEY = os.getenv("BINANCE_SECRET_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# 데이터베이스 설정
DB_FILE = 'bitcoin_trades.db'

# ==========================================================
# 2. API 연결 초기화
# ==========================================================

# CCXT 바이낸스 거래소 초기화
try:
    exchange = ccxt.binance({
        'apiKey': BINANCE_API_KEY,
        'secret': BINANCE_SECRET_KEY,
        'enableRateLimit': True,
        'options': {
            'defaultType': 'future',  # 선물 거래 사용
        }
    })
    print("바이낸스 API 연결 성공")
except Exception as e:
    print(f"바이낸스 API 연결 실패: {e}")
    exchange = None

# OpenAI 클라이언트 초기화
try:
    client = OpenAI(api_key=OPENAI_API_KEY)
    print("OpenAI API 연결 성공")
except Exception as e:
    print(f"OpenAI API 연결 실패: {e}")
    client = None

# ==========================================================
# 3. 데이터베이스 관리 함수
# ==========================================================

def init_database():
    """
    SQLite 데이터베이스 초기화 및 필요한 테이블 생성
    
    테이블 구조:
    1. trades: 거래 기록 저장
    2. news: 뉴스 내용 및 감성 점수 저장
    3. trade_results: 종료된 거래의 결과 저장
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # 거래 내역 테이블 생성
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,              -- 거래 시작 시간
        direction TEXT NOT NULL,              -- 'long' 또는 'short'
        entry_price REAL NOT NULL,            -- 진입 가격
        amount REAL NOT NULL,                 -- 거래량 (BTC)
        leverage INTEGER NOT NULL,            -- 레버리지 배수
        position_size REAL NOT NULL,          -- 자본 대비 포지션 크기 비율
        investment_amount REAL NOT NULL,      -- 실제 투자 금액 (USDT)
        stop_loss_price REAL NOT NULL,        -- 손절가
        take_profit_price REAL NOT NULL,      -- 익절가
        stop_loss_percentage REAL NOT NULL,   -- 진입가 대비 손절 비율
        take_profit_percentage REAL NOT NULL, -- 진입가 대비 익절 비율
        risk_reward_ratio REAL NOT NULL,      -- 위험:보상 비율
        probability REAL NOT NULL,            -- AI가 예측한 성공 확률
        sentiment_score INTEGER,              -- 뉴스 감성 점수
        status TEXT DEFAULT 'open'            -- 상태('open' 또는 'closed')
    )
    ''')
    
    # 뉴스 내역 테이블 생성
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS news (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,         -- 뉴스 수집 시간
        news_content TEXT NOT NULL,      -- 뉴스 내용
        sentiment_score INTEGER NOT NULL -- 뉴스 감성 점수 (-10 ~ +10)
    )
    ''')
    
    # 거래 결과 테이블 생성
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS trade_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_id INTEGER NOT NULL,               -- trades 테이블 외래 키
        close_timestamp TEXT NOT NULL,           -- 거래 종료 시간
        close_price REAL NOT NULL,               -- 종료 가격
        profit_loss REAL NOT NULL,               -- 손익 금액
        profit_loss_percentage REAL NOT NULL,    -- 손익 백분율
        result_type TEXT NOT NULL,               -- 'take_profit' 또는 'stop_loss'
        FOREIGN KEY (trade_id) REFERENCES trades(id)
    )
    ''')
    
    conn.commit()
    conn.close()
    print(f"데이터베이스 초기화 완료: {DB_FILE}")

def save_trade_to_db(direction, entry_price, amount, leverage, position_size, investment_amount, 
                     sl_price, tp_price, sl_percentage, tp_percentage, risk_reward_ratio, probability, sentiment_score):
    """
    새로운 거래 내역을 데이터베이스에 저장
    
    Parameters:
        direction (str): 'long' 또는 'short'
        entry_price (float): 진입 가격
        amount (float): 비트코인 수량
        leverage (int): 레버리지 배수
        position_size (float): 자본 대비 투자 비율 (0.0 ~ 1.0)
        investment_amount (float): 실제 투자 금액 (USDT)
        sl_price (float): 손절가
        tp_price (float): 익절가
        sl_percentage (float): 손절 비율 (%)
        tp_percentage (float): 익절 비율 (%)
        risk_reward_ratio (float): 위험:보상 비율
        probability (float): 예상 성공 확률
        sentiment_score (int): 뉴스 감성 점수
    
    Returns:
        int: 생성된 거래 ID
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    cursor.execute('''
    INSERT INTO trades (
        timestamp, direction, entry_price, amount, leverage, position_size, investment_amount,
        stop_loss_price, take_profit_price, stop_loss_percentage, take_profit_percentage, 
        risk_reward_ratio, probability, sentiment_score, status
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        timestamp, direction, entry_price, amount, leverage, position_size, investment_amount,
        sl_price, tp_price, sl_percentage, tp_percentage, risk_reward_ratio, probability, 
        sentiment_score, 'open'
    ))
    
    trade_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    return trade_id

def save_news_to_db(news_content, sentiment_score):
    """
    수집한 뉴스와 감성 점수를 데이터베이스에 저장
    
    Parameters:
        news_content (str): 수집된 뉴스 내용
        sentiment_score (int): 뉴스 감성 점수 (-10 ~ +10)
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    cursor.execute('''
    INSERT INTO news (timestamp, news_content, sentiment_score)
    VALUES (?, ?, ?)
    ''', (timestamp, news_content, sentiment_score))
    
    conn.commit()
    conn.close()

def update_trade_result(trade_id, close_price, result_type):
    """
    거래 종료 시 결과를 데이터베이스에 업데이트
    
    Parameters:
        trade_id (int): 거래 ID
        close_price (float): 종료 가격
        result_type (str): 'take_profit' 또는 'stop_loss'
    
    Returns:
        bool: 업데이트 성공 여부
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # 원래 거래 정보 가져오기
    cursor.execute("SELECT entry_price, amount, direction FROM trades WHERE id = ?", (trade_id,))
    trade_info = cursor.fetchone()
    
    if not trade_info:
        print(f"거래 ID {trade_id}를 찾을 수 없습니다.")
        conn.close()
        return False
    
    entry_price, amount, direction = trade_info
    close_timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # 손익 계산
    if direction == 'long':
        # 롱 포지션: (종가 - 진입가) * 수량
        profit_loss = (close_price - entry_price) * amount
        profit_loss_percentage = ((close_price / entry_price) - 1) * 100
    else:  # short
        # 숏 포지션: (진입가 - 종가) * 수량
        profit_loss = (entry_price - close_price) * amount
        profit_loss_percentage = ((entry_price / close_price) - 1) * 100
    
    # 거래 상태 업데이트
    cursor.execute("UPDATE trades SET status = 'closed' WHERE id = ?", (trade_id,))
    
    # 거래 결과 저장
    cursor.execute('''
    INSERT INTO trade_results (trade_id, close_timestamp, close_price, profit_loss, profit_loss_percentage, result_type)
    VALUES (?, ?, ?, ?, ?, ?)
    ''', (trade_id, close_timestamp, close_price, profit_loss, profit_loss_percentage, result_type))
    
    conn.commit()
    conn.close()
    
    return True

def get_open_trades():
    """
    현재 열려 있는 모든 거래 목록 가져오기
    
    Returns:
        list: 열린 거래 목록
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM trades WHERE status = 'open'")
    open_trades = cursor.fetchall()
    
    conn.close()
    return open_trades

def get_trade_history(limit=10):
    """
    최근 거래 내역 가져오기
    
    Parameters:
        limit (int): 가져올 거래 내역 수
    
    Returns:
        list: 거래 내역 목록 (딕셔너리 형태)
    """
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row  # 결과를 딕셔너리 형태로 반환
    cursor = conn.cursor()
    
    cursor.execute('''
    SELECT t.*, tr.close_timestamp, tr.close_price, tr.profit_loss, tr.profit_loss_percentage, tr.result_type
    FROM trades t
    LEFT JOIN trade_results tr ON t.id = tr.trade_id
    ORDER BY t.timestamp DESC
    LIMIT ?
    ''', (limit,))
    
    trades = [dict(row) for row in cursor.fetchall()]
    
    conn.close()
    return trades

def get_trade_statistics():
    """
    전체 거래 통계 정보 계산
    
    Returns:
        dict: 거래 통계 정보
            - total_trades: 총 거래 수
            - closed_trades: 완료된 거래 수
            - winning_trades: 이익 거래 수
            - win_rate: 승률 (%)
            - avg_profit_loss: 평균 손익률 (%)
            - max_profit: 최대 이익률 (%)
            - max_loss: 최대 손실률 (%)
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # 전체 거래 수
    cursor.execute("SELECT COUNT(*) FROM trades")
    total_trades = cursor.fetchone()[0]
    
    # 완료된 거래 수
    cursor.execute("SELECT COUNT(*) FROM trades WHERE status = 'closed'")
    closed_trades = cursor.fetchone()[0]
    
    # 승리 거래 수 (양수 손익)
    cursor.execute('''
    SELECT COUNT(*) FROM trade_results WHERE profit_loss > 0
    ''')
    winning_trades = cursor.fetchone()[0]
    
    # 승률
    win_rate = (winning_trades / closed_trades * 100) if closed_trades > 0 else 0
    
    # 평균 수익/손실
    cursor.execute('''
    SELECT AVG(profit_loss_percentage) FROM trade_results
    ''')
    avg_profit_loss = cursor.fetchone()[0] or 0
    
    # 최대 수익/손실
    cursor.execute('''
    SELECT MAX(profit_loss_percentage) FROM trade_results
    ''')
    max_profit = cursor.fetchone()[0] or 0
    
    cursor.execute('''
    SELECT MIN(profit_loss_percentage) FROM trade_results
    ''')
    max_loss = cursor.fetchone()[0] or 0
    
    conn.close()
    
    return {
        "total_trades": total_trades,
        "closed_trades": closed_trades,
        "winning_trades": winning_trades,
        "win_rate": win_rate,
        "avg_profit_loss": avg_profit_loss,
        "max_profit": max_profit,
        "max_loss": max_loss
    }

def get_historical_trades_analysis():
    """
    데이터베이스에서 과거 거래 내역을 분석하여 AI 학습에 활용할 데이터를 제공
    
    Returns:
        dict: 거래 분석 데이터
            - overall_stats: 전체 통계
            - direction_performance: 방향별 성능
            - leverage_performance: 레버리지별 성능
            - sentiment_performance: 감성 점수별 성능
            - hourly_performance: 시간대별 성능
            - recent_trades: 최근 거래 상세 정보
            - pattern_insights: 성공/실패 패턴 분석
    """
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    historical_analysis = {
        'overall_stats': {},
        'direction_performance': [],
        'leverage_performance': [],
        'sentiment_performance': [],
        'hourly_performance': [],
        'recent_trades': [],
        'pattern_insights': {}
    }
    
    # 전체 통계
    stats = get_trade_statistics()
    historical_analysis['overall_stats'] = stats
    
    # 방향별 성능
    cursor.execute('''
    SELECT direction, COUNT(*) as count, 
           (SELECT COUNT(*) FROM trades t JOIN trade_results tr ON t.id = tr.trade_id 
            WHERE t.direction = trades.direction AND tr.profit_loss > 0) as wins,
           (SELECT AVG(profit_loss_percentage) FROM trades t JOIN trade_results tr ON t.id = tr.trade_id 
            WHERE t.direction = trades.direction) as avg_profit
    FROM trades
    WHERE status = 'closed'
    GROUP BY direction
    ''')
    direction_stats = cursor.fetchall()
    
    for stat in direction_stats:
        win_rate = (stat['wins'] / stat['count'] * 100) if stat['count'] > 0 else 0
        historical_analysis['direction_performance'].append({
            'direction': stat['direction'],
            'count': stat['count'],
            'wins': stat['wins'],
            'win_rate': win_rate,
            'avg_profit': stat['avg_profit'] or 0
        })
    
    # 레버리지별 성능
    cursor.execute('''
    SELECT leverage, COUNT(*) as count, 
           (SELECT COUNT(*) FROM trades t JOIN trade_results tr ON t.id = tr.trade_id 
            WHERE t.leverage = trades.leverage AND tr.profit_loss > 0) as wins,
           (SELECT AVG(profit_loss_percentage) FROM trades t JOIN trade_results tr ON t.id = tr.trade_id 
            WHERE t.leverage = trades.leverage) as avg_profit
    FROM trades
    WHERE status = 'closed'
    GROUP BY leverage
    ORDER BY leverage
    ''')
    leverage_stats = cursor.fetchall()
    
    for stat in leverage_stats:
        win_rate = (stat['wins'] / stat['count'] * 100) if stat['count'] > 0 else 0
        historical_analysis['leverage_performance'].append({
            'leverage': stat['leverage'],
            'count': stat['count'],
            'wins': stat['wins'],
            'win_rate': win_rate,
            'avg_profit': stat['avg_profit'] or 0
        })
    
    # 감성 점수별 성능
    cursor.execute('''
    SELECT 
        sentiment_score,
        COUNT(*) as count,
        (SELECT COUNT(*) FROM trades t JOIN trade_results tr ON t.id = tr.trade_id 
         WHERE t.sentiment_score = trades.sentiment_score AND tr.profit_loss > 0) as wins,
        (SELECT AVG(profit_loss_percentage) FROM trades t JOIN trade_results tr ON t.id = tr.trade_id 
         WHERE t.sentiment_score = trades.sentiment_score) as avg_profit
    FROM trades
    WHERE status = 'closed'
    GROUP BY sentiment_score
    ORDER BY sentiment_score
    ''')
    sentiment_stats = cursor.fetchall()
    
    for stat in sentiment_stats:
        win_rate = (stat['wins'] / stat['count'] * 100) if stat['count'] > 0 else 0
        historical_analysis['sentiment_performance'].append({
            'sentiment_score': stat['sentiment_score'],
            'count': stat['count'],
            'wins': stat['wins'],
            'win_rate': win_rate,
            'avg_profit': stat['avg_profit'] or 0
        })
    
    # 시간대별 성능
    cursor.execute('''
    SELECT 
        SUBSTR(timestamp, 12, 2) as hour,
        COUNT(*) as count,
        (SELECT COUNT(*) FROM trades t JOIN trade_results tr ON t.id = tr.trade_id 
         WHERE SUBSTR(t.timestamp, 12, 2) = SUBSTR(trades.timestamp, 12, 2) AND tr.profit_loss > 0) as wins,
        (SELECT AVG(profit_loss_percentage) FROM trades t JOIN trade_results tr ON t.id = tr.trade_id 
         WHERE SUBSTR(t.timestamp, 12, 2) = SUBSTR(trades.timestamp, 12, 2)) as avg_profit
    FROM trades
    WHERE status = 'closed'
    GROUP BY hour
    ORDER BY hour
    ''')
    hourly_stats = cursor.fetchall()
    
    for stat in hourly_stats:
        win_rate = (stat['wins'] / stat['count'] * 100) if stat['count'] > 0 else 0
        historical_analysis['hourly_performance'].append({
            'hour': stat['hour'],
            'count': stat['count'],
            'wins': stat['wins'],
            'win_rate': win_rate,
            'avg_profit': stat['avg_profit'] or 0
        })
    
    # 최근 30개 거래의 상세 정보 (AI 학습용)
    cursor.execute('''
    SELECT t.*, tr.close_timestamp, tr.close_price, tr.profit_loss, tr.profit_loss_percentage, tr.result_type
    FROM trades t
    JOIN trade_results tr ON t.id = tr.trade_id
    WHERE t.status = 'closed'
    ORDER BY t.timestamp DESC
    LIMIT 30
    ''')
    recent_trades = cursor.fetchall()
    
    for trade in recent_trades:
        historical_analysis['recent_trades'].append({
            'id': trade['id'],
            'timestamp': trade['timestamp'],
            'direction': trade['direction'],
            'entry_price': trade['entry_price'],
            'leverage': trade['leverage'],
            'position_size': trade['position_size'],
            'stop_loss_percentage': trade['stop_loss_percentage'],
            'take_profit_percentage': trade['take_profit_percentage'],
            'probability': trade['probability'],
            'sentiment_score': trade['sentiment_score'],
            'close_timestamp': trade['close_timestamp'],
            'close_price': trade['close_price'],
            'profit_loss': trade['profit_loss'],
            'profit_loss_percentage': trade['profit_loss_percentage'],
            'result_type': trade['result_type']
        })
    
    # 패턴 인사이트 - 이전에 성공했던 거래 패턴 분석
    if len(recent_trades) > 5:
        # 성공적인 거래 패턴 분석
        cursor.execute('''
        SELECT 
            t.direction,
            t.leverage,
            CASE 
                WHEN t.sentiment_score < -5 THEN 'very_negative'
                WHEN t.sentiment_score < 0 THEN 'negative'
                WHEN t.sentiment_score = 0 THEN 'neutral'
                WHEN t.sentiment_score <= 5 THEN 'positive'
                ELSE 'very_positive'
            END as sentiment_category,
            AVG(t.stop_loss_percentage) as avg_sl,
            AVG(t.take_profit_percentage) as avg_tp,
            COUNT(*) as count,
            AVG(tr.profit_loss_percentage) as avg_profit
        FROM trades t
        JOIN trade_results tr ON t.id = tr.trade_id
        WHERE tr.profit_loss > 0
        GROUP BY t.direction, t.leverage, sentiment_category
        HAVING count >= 2
        ORDER BY avg_profit DESC
        LIMIT 5
        ''')
        winning_patterns = cursor.fetchall()
        
        historical_analysis['pattern_insights']['winning_patterns'] = []
        for pattern in winning_patterns:
            historical_analysis['pattern_insights']['winning_patterns'].append({
                'direction': pattern['direction'],
                'leverage': pattern['leverage'],
                'sentiment_category': pattern['sentiment_category'],
                'avg_sl': pattern['avg_sl'],
                'avg_tp': pattern['avg_tp'],
                'count': pattern['count'],
                'avg_profit': pattern['avg_profit']
            })
        
        # 실패한 거래 패턴 분석
        cursor.execute('''
        SELECT 
            t.direction,
            t.leverage,
            CASE 
                WHEN t.sentiment_score < -5 THEN 'very_negative'
                WHEN t.sentiment_score < 0 THEN 'negative'
                WHEN t.sentiment_score = 0 THEN 'neutral'
                WHEN t.sentiment_score <= 5 THEN 'positive'
                ELSE 'very_positive'
            END as sentiment_category,
            AVG(t.stop_loss_percentage) as avg_sl,
            AVG(t.take_profit_percentage) as avg_tp,
            COUNT(*) as count,
            AVG(tr.profit_loss_percentage) as avg_profit
        FROM trades t
        JOIN trade_results tr ON t.id = tr.trade_id
        WHERE tr.profit_loss <= 0
        GROUP BY t.direction, t.leverage, sentiment_category
        HAVING count >= 2
        ORDER BY avg_profit ASC
        LIMIT 5
        ''')
        losing_patterns = cursor.fetchall()
        
        historical_analysis['pattern_insights']['losing_patterns'] = []
        for pattern in losing_patterns:
            historical_analysis['pattern_insights']['losing_patterns'].append({
                'direction': pattern['direction'],
                'leverage': pattern['leverage'],
                'sentiment_category': pattern['sentiment_category'],
                'avg_sl': pattern['avg_sl'],
                'avg_tp': pattern['avg_tp'],
                'count': pattern['count'],
                'avg_profit': pattern['avg_profit']
            })
    
    conn.close()
    return historical_analysis

# ==========================================================
# 4. 기술적 분석 함수
# ==========================================================

def calculate_rsi(series, period=14):
    """
    상대강도지수(RSI) 계산
    
    Parameters:
        series (pandas.Series): 가격 시리즈
        period (int): RSI 계산 기간 (기본값: 14)
    
    Returns:
        pandas.Series: 계산된 RSI 값
    """
    delta = series.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def fetch_ohlcv_data(exchange, symbol):
    """
    거래소에서 멀티 타임프레임 OHLCV 데이터 가져오기
    
    Parameters:
        exchange (ccxt.Exchange): CCXT 거래소 객체
        symbol (str): 거래 페어 (예: 'BTC/USDT')
    
    Returns:
        tuple: (15분봉 데이터, 4시간봉 데이터, 일봉 데이터)
    """
    # 15분봉 데이터 (단기 - 24시간분)
    ohlcv_15m = exchange.fetch_ohlcv(symbol, timeframe="15m", limit=96)
    df_15m = pd.DataFrame(ohlcv_15m, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df_15m['timestamp'] = pd.to_datetime(df_15m['timestamp'], unit='ms')
    
    # 4시간봉 데이터 (중기 - 8일분)
    ohlcv_4h = exchange.fetch_ohlcv(symbol, timeframe="4h", limit=48)
    df_4h = pd.DataFrame(ohlcv_4h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df_4h['timestamp'] = pd.to_datetime(df_4h['timestamp'], unit='ms')
    
    # 일봉 데이터 (장기 - 한 달분)
    ohlcv_1d = exchange.fetch_ohlcv(symbol, timeframe="1d", limit=30)
    df_1d = pd.DataFrame(ohlcv_1d, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df_1d['timestamp'] = pd.to_datetime(df_1d['timestamp'], unit='ms')
    
    return df_15m, df_4h, df_1d

def calculate_stats(df):
    """
    OHLCV 데이터에서 기술적 지표 및 통계 계산
    
    Parameters:
        df (pandas.DataFrame): OHLCV 데이터프레임
    
    Returns:
        dict: 계산된 통계 및 지표
    """
    stats = {}
    # 기본 가격 지표
    stats['last_close'] = df['close'].iloc[-1]  # 최근 종가
    stats['avg_5'] = df['close'].rolling(5).mean().iloc[-1]  # 5기간 이동평균
    stats['avg_10'] = df['close'].rolling(10).mean().iloc[-1]  # 10기간 이동평균
    stats['avg_20'] = df['close'].rolling(20).mean().iloc[-1]  # 20기간 이동평균
    
    # 볼륨 지표
    stats['vol_avg'] = df['volume'].mean()  # 평균 거래량
    stats['last_vol'] = df['volume'].iloc[-1]  # 최근 거래량
    
    # 가격 범위
    stats['highest_high'] = df['high'].max()  # 최고가
    stats['lowest_low'] = df['low'].min()  # 최저가
    
    # 추가적인 기술적 지표
    stats['rsi_14'] = calculate_rsi(df['close'], 14).iloc[-1] if len(df) > 14 else 50  # RSI
    
    return stats

def prepare_analysis_data(df_15m, df_4h, df_1d, stats_15m, stats_4h, stats_1d, current_price):
    """
    차트 데이터를 AI 분석을 위한 형태로 변환
    
    Parameters:
        df_15m, df_4h, df_1d (pandas.DataFrame): 다양한 타임프레임의 OHLCV 데이터
        stats_15m, stats_4h, stats_1d (dict): 다양한 타임프레임의 통계 데이터
        current_price (float): 현재 가격
    
    Returns:
        dict: AI 분석용 데이터 구조
    """
    return {
        '15m': [
            {
                'timestamp': row['timestamp'].isoformat(),
                'open': row['open'],
                'high': row['high'],
                'low': row['low'],
                'close': row['close'],
                'volume': row['volume']
            } for row in df_15m.to_dict(orient='records')
        ],
        '4h': [
            {
                'timestamp': row['timestamp'].isoformat(),
                'open': row['open'],
                'high': row['high'],
                'low': row['low'],
                'close': row['close'],
                'volume': row['volume']
            } for row in df_4h.to_dict(orient='records')
        ],
        '1d': [
            {
                'timestamp': row['timestamp'].isoformat(),
                'open': row['open'],
                'high': row['high'],
                'low': row['low'],
                'close': row['close'],
                'volume': row['volume']
            } for row in df_1d.to_dict(orient='records')
        ],
        'stats': {
            '15m': stats_15m,  # 15분봉 통계
            '4h': stats_4h,    # 4시간봉 통계
            '1d': stats_1d     # 일봉 통계
        },
        'current_price': current_price  # 현재 가격
    }

# ==========================================================
# 5. 뉴스 및 감성 분석 함수
# ==========================================================

def fetch_bitcoin_news(client):
    """
    OpenAI API를 사용하여 최신 비트코인 뉴스 가져오기
    
    Parameters:
        client (OpenAI): OpenAI API 클라이언트
        
    Returns:
        str: 최신 비트코인 뉴스 요약
    """
    try:
        current_date = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        
        # OpenAI Web Search API를 사용하여 최신 뉴스 가져오기
        news_response = client.responses.create(
            model="gpt-4.1",  # Web Search 지원 모델
            tools=[{
                "type": "web_search_preview", 
                "search_context_size": "medium"
            }],
            input=f"""What are the most significant Bitcoin news from {yesterday} to {current_date}? 
                     Focus on price movements, regulations, institutional adoption, and market sentiment. 
                     List only the 3-5 most impactful headlines with brief factual summaries."""
        )
        
        return news_response.output_text
    
    except Exception as e:
        print(f"뉴스 가져오기 오류: {e}")
        return "Unable to fetch recent Bitcoin news due to an error."

def analyze_news_sentiment(client, news_text):
    """
    OpenAI API를 사용하여 뉴스 감성 분석
    
    Parameters:
        client (OpenAI): OpenAI API 클라이언트
        news_text (str): 분석할 뉴스 텍스트
        
    Returns:
        int: 감성 점수 (-10 ~ +10)
            -10 ~ -7: 매우 부정적
            -6 ~ -4: 중간 정도 부정적
            -3 ~ -1: 약간 부정적
            0: 중립
            +1 ~ +3: 약간 긍정적
            +4 ~ +6: 중간 정도 긍정적
            +7 ~ +10: 매우 긍정적
    """
    try:
        # OpenAI API를 사용한 감성 분석
        sentiment_response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": """
                You are a financial news sentiment analyzer. 
                Analyze the provided Bitcoin news and rate the overall sentiment on a scale from -10 to +10:
                
                -10 to -7: Extremely bearish
                -6 to -4: Moderately bearish
                -3 to -1: Slightly bearish
                0: Neutral
                +1 to +3: Slightly bullish
                +4 to +6: Moderately bullish
                +7 to +10: Extremely bullish
                
                Respond with ONLY the numerical score.
                """},
                {"role": "user", "content": news_text}
            ]
        )
        
        sentiment_score = sentiment_response.choices[0].message.content.strip()
        # 숫자만 추출 (정규 표현식 사용)
        score_match = re.search(r'(-?\d+)', sentiment_score)
        if score_match:
            return int(score_match.group(1))
        return 0  # 중립으로 기본 설정
        
    except Exception as e:
        print(f"감성 분석 오류: {e}")
        return 0  # 오류 시 중립으로 반환

# ==========================================================
# 6. 거래 실행 함수
# ==========================================================

def check_position(exchange, symbol):
    """
    현재 보유 중인 포지션 확인
    
    Parameters:
        exchange (ccxt.Exchange): CCXT 거래소 객체
        symbol (str): 거래 페어 (예: 'BTC/USDT')
        
    Returns:
        tuple: (포지션 방향 ('long', 'short' 또는 None), 포지션 크기)
    """
    try:
        # 바이낸스 선물에서 포지션 정보 가져오기
        positions = exchange.fetch_positions([symbol])
        
        if not positions:
            return None, 0
        
        for position in positions:
            # 포지션 크기가 0이 아닌 경우에만 포지션 정보 반환
            size = float(position.get('contracts', 0))
            if size > 0:
                side = position.get('side')
                # 바이낸스는 'long' 또는 'short'를 반환
                return side, size
        
        # 포지션이 없으면 None, 0 반환
        return None, 0
        
    except Exception as e:
        print(f"포지션 확인 오류: {e}")
        # 오류 발생 시 안전하게 None, 0 반환
        return None, 0

def check_and_update_positions(exchange, symbol):
    """
    포지션 상태를 확인하고 닫힌 포지션을 DB에 업데이트
    
    Parameters:
        exchange (ccxt.Exchange): CCXT 거래소 객체
        symbol (str): 거래 페어 (예: 'BTC/USDT')
        
    Returns:
        tuple: (현재 포지션 방향, 포지션 크기)
    """
    # DB에서 열려있는 거래 가져오기
    open_trades = get_open_trades()
    
    # 현재 포지션 정보
    current_side, current_amount = check_position(exchange, symbol)
    
    if not open_trades:
        return current_side, current_amount
    
    current_price = exchange.fetch_ticker(symbol)['last']
    
    # 포지션이 없는데 DB에 열린 거래가 있다면, 포지션이 닫혔다는 의미
    if not current_side and open_trades:
        for trade in open_trades:
            trade_id = trade[0]  # id는 첫 번째 컬럼
            direction = trade[2]  # direction은 세 번째 컬럼
            
            # 손절가와 익절가 가져오기
            sl_price = trade[8]  # stop_loss_price
            tp_price = trade[9]  # take_profit_price
            
            # 가격에 따라 손절 또는 익절 결정
            if direction == 'long':
                if current_price <= sl_price:
                    result_type = 'stop_loss'
                    close_price = sl_price
                else:
                    result_type = 'take_profit'
                    close_price = tp_price
            else:  # short
                if current_price >= sl_price:
                    result_type = 'stop_loss'
                    close_price = sl_price
                else:
                    result_type = 'take_profit'
                    close_price = tp_price
            
            # DB 업데이트
            update_trade_result(trade_id, close_price, result_type)
            print(f"거래 ID {trade_id}가 {result_type}으로 종료되었습니다. 마감 가격: ${close_price:,.2f}")
    
    return current_side, current_amount

def cancel_all_orders(exchange, symbol):
    """
    모든 미체결 주문 취소
    
    Parameters:
        exchange (ccxt.Exchange): CCXT 거래소 객체
        symbol (str): 거래 페어 (예: 'BTC/USDT')
        
    Returns:
        bool: 취소 성공 여부
    """
    try:
        # 미체결 주문 확인
        open_orders = exchange.fetch_open_orders(symbol)
        if open_orders:
            # 모든 미체결 주문 취소
            for order in open_orders:
                exchange.cancel_order(order['id'], symbol)
            print(f"{symbol} 모든 미체결 주문 취소됨")
            return True
        else:
            print("취소할 미체결 주문이 없습니다.")
            return False
    except Exception as e:
        print(f"주문 취소 오류: {e}")
        return False

def execute_order(exchange, symbol, action, position_size, leverage, sl_percentage, tp_percentage, current_price, probability, sentiment_score):
    """
    거래 주문 실행
    
    Parameters:
        exchange (ccxt.Exchange): CCXT 거래소 객체
        symbol (str): 거래 페어 (예: 'BTC/USDT')
        action (str): 'long' 또는 'short'
        position_size (float): 자본 대비 투자 비율 (0.0 ~ 1.0)
        leverage (int): 레버리지 배수
        sl_percentage (float): 손절 비율 (%)
        tp_percentage (float): 익절 비율 (%)
        current_price (float): 현재 가격
        probability (float): 예상 성공 확률
        sentiment_score (int): 뉴스 감성 점수
        
    Returns:
        tuple: (주문 실행 성공 여부, 거래 ID)
    """
    try:
        # 레버리지 설정
        exchange.set_leverage(leverage, symbol)
        
        # 계좌 잔액 확인
        balance = exchange.fetch_balance()
        usdt_balance = balance['total']['USDT']
        
        # 투자 금액 계산
        investment_amount = usdt_balance * position_size
        
        # 비트코인 수량 계산 (레버리지 고려)
        amount = (investment_amount * leverage) / current_price
        # 소수점 3자리까지 내림 (바이낸스 최소 단위)
        amount = math.floor(amount * 1000) / 1000
        
        if action == "long":
            # 롱 포지션 진입 (시장가 매수)
            order = exchange.create_market_buy_order(symbol, amount)
            entry_price = current_price
            
            # 손절가 계산 (진입가 아래)
            sl_price = round(entry_price * (1 - sl_percentage/100), 2)
            # 익절가 계산 (진입가 위)
            tp_price = round(entry_price * (1 + tp_percentage/100), 2)
            
            # 손절 주문 생성
            exchange.create_order(symbol, 'STOP_MARKET', 'sell', amount, None, {'stopPrice': sl_price})
            # 익절 주문 생성
            exchange.create_order(symbol, 'TAKE_PROFIT_MARKET', 'sell', amount, None, {'stopPrice': tp_price})
            
            # 거래 정보 출력
            risk_reward_ratio = tp_percentage/sl_percentage
            print(f"\n=== LONG 포지션 개설 ===")
            print(f"투자 비율: 자본의 {position_size:.1%} (${investment_amount:.2f})")
            print(f"레버리지: {leverage}배")
            print(f"BTC 수량: {amount:.4f} BTC")
            print(f"진입가: ${entry_price:,.2f}")
            print(f"손절가: ${sl_price:,.2f} (-{sl_percentage}%)")
            print(f"익절가: ${tp_price:,.2f} (+{tp_percentage}%)")
            print(f"위험:보상 비율: 1:{risk_reward_ratio:.2f}")
            print("===========================")
            
            # 데이터베이스에 거래 정보 저장
            trade_id = save_trade_to_db(
                'long', entry_price, amount, leverage, position_size, investment_amount,
                sl_price, tp_price, sl_percentage, tp_percentage, risk_reward_ratio, 
                probability, sentiment_score
            )
            print(f"거래 ID {trade_id} 데이터베이스에 저장됨")
            
            return True, trade_id
            
        elif action == "short":
            # 숏 포지션 진입 (시장가 매도)
            order = exchange.create_market_sell_order(symbol, amount)
            entry_price = current_price
            
            # 손절가 계산 (진입가 위)
            sl_price = round(entry_price * (1 + sl_percentage/100), 2)
            # 익절가 계산 (진입가 아래)
            tp_price = round(entry_price * (1 - tp_percentage/100), 2)
            
            # 손절 주문 생성
            exchange.create_order(symbol, 'STOP_MARKET', 'buy', amount, None, {'stopPrice': sl_price})
            # 익절 주문 생성
            exchange.create_order(symbol, 'TAKE_PROFIT_MARKET', 'buy', amount, None, {'stopPrice': tp_price})
            
            # 거래 정보 출력
            risk_reward_ratio = tp_percentage/sl_percentage
            print(f"\n=== SHORT 포지션 개설 ===")
            print(f"투자 비율: 자본의 {position_size:.1%} (${investment_amount:.2f})")
            print(f"레버리지: {leverage}배")
            print(f"BTC 수량: {amount:.4f} BTC")
            print(f"진입가: ${entry_price:,.2f}")
            print(f"손절가: ${sl_price:,.2f} (+{sl_percentage}%)")
            print(f"익절가: ${tp_price:,.2f} (-{tp_percentage}%)")
            print(f"위험:보상 비율: 1:{risk_reward_ratio:.2f}")
            print("============================")
            
            # 데이터베이스에 거래 정보 저장
            trade_id = save_trade_to_db(
                'short', entry_price, amount, leverage, position_size, investment_amount,
                sl_price, tp_price, sl_percentage, tp_percentage, risk_reward_ratio,
                probability, sentiment_score
            )
            print(f"거래 ID {trade_id} 데이터베이스에 저장됨")
            
            return True, trade_id
            
        return False, None
    
    except Exception as e:
        print(f"주문 실행 오류: {e}")
        return False, None

# ==========================================================
# 7. AI 의사결정 함수
# ==========================================================

def analyze_with_dynamic_optimization(analysis_data, bitcoin_news, sentiment_score):
    """
    거래 전략을 동적으로 최적화하는 AI 분석 함수
    
    특징:
    - 멀티 타임프레임 분석
    - 뉴스 감성 분석 결합
    - 과거 거래 기반 자가 학습
    - 켈리 기준 기반 포지션 사이징
    - 동적 레버리지 및 SL/TP 최적화
    
    Parameters:
        analysis_data (dict): 기술적 분석 데이터
        bitcoin_news (str): 최신 비트코인 뉴스
        sentiment_score (int): 뉴스 감성 점수 (-10 ~ +10)
    
    Returns:
        tuple: (
            방향: 'long', 'short', 또는 'wait',
            성공 확률: 0.0 ~ 1.0,
            포지션 크기: 0.0 ~ 1.0,
            레버리지: 1 ~ 10,
            손절 비율: %,
            익절 비율: %,
            의사결정 이유
        )
    """
    # 과거 거래 데이터 가져오기
    historical_trades = get_historical_trades_analysis()
    
    # AI 분석을 위한 시스템 프롬프트 (과거 거래 기반 자가 학습 기능 포함)
    system_prompt = f"""
    You are a crypto trading expert specializing in multi-timeframe analysis, news sentiment analysis, and optimized trading parameters. 
    You have the ability to learn from past trades and continuously improve your performance.

    Analyze the following data points:

    1. Technical Analysis:
    - 15m (96 bars): Short-term trend and momentum
    - 4h (48 bars): Medium-term trend
    - 1d (30 bars): Long-term trend

    2. Market News:
    - Latest Bitcoin news and headlines
    - News sentiment score: {sentiment_score} (scale from -10 to +10)

    3. Historical Trade Performance:
    - Overall statistics: Win rate, average profit/loss
    - Direction performance: Success patterns for long vs short positions
    - Leverage performance: Optimal leverage levels based on historical trades
    - Sentiment-based performance: Correlation between news sentiment and trade outcomes
    - Time-based performance: Optimal trading hours
    - Recent trades: Detailed analysis of recent trades (wins and losses)
    - Pattern insights: Identifying successful and unsuccessful trading patterns

    4. Optimized Trading Parameters:
    - Position sizing using Kelly Criterion: f* = (p/a) - (q/b)
    - Optimal leverage determination (between 1x and 10x)
    - Optimal stop-loss (SL) and take-profit (TP) placement
    - Parameter adjustments based on market volatility and trend strength

    Trading Parameter Optimization Guidelines:
    - Low volatility with strong trend: Relatively higher leverage, wider SL/TP
    - High volatility: Lower leverage, wider SL/TP
    - Range-bound market: Lower leverage, SL/TP based on range boundaries
    - Strong momentum confirmation: Appropriate leverage with SL/TP based on technical levels

    Leverage Decision Factors:
    - Volatility: Consider ATR (Average True Range) or Bollinger Band width
    - Trend strength: Arrangement and slope of moving averages across timeframes
    - Volume: Volume increase patterns for trend confirmation
    - Market environment: General market risk and news impact
    - Historical performance: Leverage levels that performed best in similar conditions

    SL/TP Decision Factors:
    - Key support/resistance levels: Previous highs/lows, Fibonacci levels
    - Volatility-based: SL/TP as multiples of ATR
    - Price swing patterns: Typical size of price swings
    - Risk:reward ratio: Maintain minimum 1:1.5 ratio
    - Historical performance: SL/TP settings that performed best in similar conditions

    Self-Learning Guidelines:
    - Identify which trading parameters led to successful trades in the past
    - Avoid repeating parameter combinations that consistently led to losses
    - Adapt to changing market conditions by analyzing recent performance
    - Consider both technical analysis and sentiment in the context of historical performance
    - Refine your decision-making process based on the most recent trading results
    - Critically analyze why certain trades failed and incorporate those learnings

    Respond in the following format:
    - DIRECTION: 'long', 'short', or 'wait'
    - PROBABILITY: Your estimated win probability (e.g., 0.65)
    - POSITION_SIZE: Percentage of capital to deploy (based on Kelly formula)
    - LEVERAGE: Optimal leverage (1x-10x)
    - STOP_LOSS: Stop-loss percentage from entry price (e.g., 0.8%)
    - TAKE_PROFIT: Take-profit percentage from entry price (e.g., 1.2%)
    - REASONING: Detailed rationale for all parameters (include key indicators, support/resistance levels, volatility analysis, self-learning insights from historical trades, etc.)
    """
    
    # AI 분석 요청
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_prompt}, 
            {"role": "user", "content": f"Technical Data: {json.dumps(analysis_data)}\n\nRecent News:\n{bitcoin_news}\n\nHistorical Trade Data: {json.dumps(historical_trades)}"}
        ]
    )
    
    # AI 결정 파싱
    response_text = response.choices[0].message.content.strip()
    lines = response_text.split('\n')

    # 기본값 설정
    direction = "wait"
    probability = 0.5
    position_size = 0
    leverage = 0
    sl_percentage = 0
    tp_percentage = 0
    reasoning = ""

    # 응답 텍스트 파싱
    for line in lines:
        line = line.strip()
        if line.startswith("DIRECTION:"):
            direction = line.replace("DIRECTION:", "").strip().lower()
        elif line.startswith("PROBABILITY:"):
            try:
                probability = float(line.replace("PROBABILITY:", "").strip())
            except ValueError:
                print("경고: 확률 값을 파싱할 수 없습니다")
        elif line.startswith("POSITION_SIZE:"):
            try:
                position_size_str = line.replace("POSITION_SIZE:", "").strip().replace("%", "")
                position_size = float(position_size_str) / 100
            except ValueError:
                print("경고: 포지션 크기 값을 파싱할 수 없습니다")
        elif line.startswith("LEVERAGE:"):
            try:
                leverage_str = line.replace("LEVERAGE:", "").strip().replace("x", "")
                leverage = int(float(leverage_str))
            except ValueError:
                print("경고: 레버리지 값을 파싱할 수 없습니다")
        elif line.startswith("STOP_LOSS:"):
            try:
                sl_str = line.replace("STOP_LOSS:", "").strip().replace("%", "")
                sl_percentage = float(sl_str)
            except ValueError:
                print("경고: 손절 값을 파싱할 수 없습니다")
        elif line.startswith("TAKE_PROFIT:"):
            try:
                tp_str = line.replace("TAKE_PROFIT:", "").strip().replace("%", "")
                tp_percentage = float(tp_str)
            except ValueError:
                print("경고: 익절 값을 파싱할 수 없습니다")
        elif line.startswith("REASONING:"):
            reasoning = line.replace("REASONING:", "").strip()
            
            # REASONING: 이후의 모든 라인을 reasoning에 추가
            reasoning_start_index = lines.index(line)
            for i in range(reasoning_start_index + 1, len(lines)):
                reasoning += "\n" + lines[i].strip()
    
    # 안전 제한 적용
    if position_size > MAX_POSITION_SIZE:
        position_size = MAX_POSITION_SIZE
        
    if leverage < MIN_LEVERAGE:
        leverage = MIN_LEVERAGE
    elif leverage > MAX_LEVERAGE:
        leverage = MAX_LEVERAGE
    
    # 최소 승률 조건 확인 (확률이 너무 낮으면 거래 안 함)
    if probability < 0.55:
        direction = "wait"
        position_size = 0
        leverage = 0
        sl_percentage = 0
        tp_percentage = 0
    
    # 과거 거래 내역에 기반한 추가 검증 (최소 5건 이상의 거래 내역이 있을 때)
    if historical_trades['overall_stats']['total_trades'] >= 5:
        # 현재 감성 점수와 유사한 상황에서의 과거 성과 확인
        sentiment_performance = None
        for perf in historical_trades['sentiment_performance']:
            if perf['sentiment_score'] == sentiment_score:
                sentiment_performance = perf
                break
        
        # 비슷한 감성 점수에서 승률이 30% 미만이었다면 더 신중하게 접근
        if sentiment_performance and sentiment_performance['win_rate'] < 30:
            print(f"경고: 감성 점수 {sentiment_score}에서 과거 성과가 좋지 않음 (승률: {sentiment_performance['win_rate']:.2f}%)")
            
            # 승률 조건 상향 조정 (더 확실한 신호에만 진입)
            if probability < 0.65:
                direction = "wait"
                position_size = 0
                leverage = 0
                sl_percentage = 0
                tp_percentage = 0
                reasoning += "\n(과거 데이터 분석 결과, 유사한 감성 점수에서 성과가 좋지 않았습니다. 더 높은 성공 확률이 필요하여 거래를 건너뜁니다.)"
        
        # 과거에 실패했던 레버리지 피하기
        leverage_to_avoid = None
        for perf in historical_trades['leverage_performance']:
            if perf['leverage'] == leverage and perf['win_rate'] < 30 and perf['count'] >= 3:
                leverage_to_avoid = leverage
                break
        
        if leverage_to_avoid:
            print(f"경고: 레버리지 {leverage_to_avoid}배에서 과거 성과가 좋지 않음 (승률: <30%)")
            # 대안 레버리지 찾기 (승률이 높았던 레버리지 중 현재 선택과 가장 가까운 것)
            alternative_leverage = None
            min_diff = float('inf')
            
            for perf in historical_trades['leverage_performance']:
                if perf['win_rate'] > 50 and perf['count'] >= 3:
                    diff = abs(perf['leverage'] - leverage)
                    if diff < min_diff:
                        min_diff = diff
                        alternative_leverage = perf['leverage']
            
            if alternative_leverage:
                leverage = alternative_leverage
                reasoning += f"\n(과거 데이터 기반으로 레버리지를 {leverage}배로 조정했습니다)"
    
    return direction, probability, position_size, leverage, sl_percentage, tp_percentage, reasoning

# ==========================================================
# 8. 주요 트레이딩 함수
# ==========================================================

def print_trade_stats():
    """
    현재까지의 거래 통계 출력
    
    Returns:
        dict: 거래 통계 정보
    """
    stats = get_trade_statistics()
    
    print("\n===== 트레이딩 봇 성능 요약 =====")
    print(f"총 거래 수: {stats['total_trades']}")
    print(f"완료된 거래: {stats['closed_trades']}")
    print(f"승리한 거래: {stats['winning_trades']}")
    print(f"승률: {stats['win_rate']:.2f}%")
    print(f"평균 수익/손실: {stats['avg_profit_loss']:.2f}%")
    print(f"최대 수익: {stats['max_profit']:.2f}%")
    print(f"최대 손실: {stats['max_loss']:.2f}%")
    print("===============================\n")
    
    # 최근 거래 내역 출력
    recent_trades = get_trade_history(5)
    if recent_trades:
        print("최근 5개 거래:")
        for i, trade in enumerate(recent_trades, 1):
            status = "종료" if trade['status'] == 'closed' else "진행 중"
            direction = "롱" if trade['direction'] == 'long' else "숏"
            print(f"{i}. {trade['timestamp']} - {direction} ${trade['entry_price']:.2f} x{trade['leverage']} ({status})")
            if trade['status'] == 'closed':
                result = "이익" if trade.get('profit_loss', 0) > 0 else "손실"
                print(f"   결과: {result} {trade.get('profit_loss_percentage', 0):.2f}% (${trade.get('profit_loss', 0):.2f})")
        print()
    
    return stats

def show_summary():
    """
    모든 거래 내역과 상세 통계를 출력
    """
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # 전체 통계
    stats = get_trade_statistics()
    
    print("\n============== BTC Trading Bot 전체 성능 요약 ==============")
    print(f"총 거래 수: {stats['total_trades']}")
    print(f"완료된 거래: {stats['closed_trades']}")
    print(f"승리한 거래: {stats['winning_trades']}")
    print(f"승률: {stats['win_rate']:.2f}%")
    print(f"평균 수익/손실: {stats['avg_profit_loss']:.2f}%")
    print(f"최대 수익: {stats['max_profit']:.2f}%")
    print(f"최대 손실: {stats['max_loss']:.2f}%")
    
    # 방향별 성능
    cursor.execute('''
    SELECT direction, COUNT(*) as count, 
           (SELECT COUNT(*) FROM trades t JOIN trade_results tr ON t.id = tr.trade_id 
            WHERE t.direction = trades.direction AND tr.profit_loss > 0) as wins
    FROM trades
    WHERE status = 'closed'
    GROUP BY direction
    ''')
    direction_stats = cursor.fetchall()
    
    print("\n방향별 성능:")
    for direction in direction_stats:
        dir_name = '롱' if direction['direction'] == 'long' else '숏'
        win_rate = (direction['wins'] / direction['count'] * 100) if direction['count'] > 0 else 0
        print(f"{dir_name}: {direction['count']}거래, {direction['wins']}승, 승률 {win_rate:.2f}%")
    
    # 레버리지별 성능
    cursor.execute('''
    SELECT leverage, COUNT(*) as count, 
           (SELECT COUNT(*) FROM trades t JOIN trade_results tr ON t.id = tr.trade_id 
            WHERE t.leverage = trades.leverage AND tr.profit_loss > 0) as wins
    FROM trades
    WHERE status = 'closed'
    GROUP BY leverage
    ORDER BY leverage
    ''')
    leverage_stats = cursor.fetchall()
    
    print("\n레버리지별 성능:")
    for leverage in leverage_stats:
        win_rate = (leverage['wins'] / leverage['count'] * 100) if leverage['count'] > 0 else 0
        print(f"{leverage['leverage']}x: {leverage['count']}거래, {leverage['wins']}승, 승률 {win_rate:.2f}%")
    
    # 감성 점수별 성능
    cursor.execute('''
    SELECT 
        CASE 
            WHEN sentiment_score < -5 THEN '매우 부정적 (-10~-6)'
            WHEN sentiment_score < 0 THEN '부정적 (-5~-1)'
            WHEN sentiment_score = 0 THEN '중립 (0)'
            WHEN sentiment_score <= 5 THEN '긍정적 (1~5)'
            ELSE '매우 긍정적 (6~10)'
        END as sentiment_range,
        COUNT(*) as count,
        (SELECT COUNT(*) FROM trades t JOIN trade_results tr ON t.id = tr.trade_id 
         WHERE tr.profit_loss > 0 AND 
         CASE 
            WHEN t.sentiment_score < -5 THEN '매우 부정적 (-10~-6)'
            WHEN t.sentiment_score < 0 THEN '부정적 (-5~-1)'
            WHEN t.sentiment_score = 0 THEN '중립 (0)'
            WHEN t.sentiment_score <= 5 THEN '긍정적 (1~5)'
            ELSE '매우 긍정적 (6~10)'
         END = sentiment_range) as wins
    FROM trades
    WHERE status = 'closed'
    GROUP BY sentiment_range
    ORDER BY MIN(sentiment_score)
    ''')
    sentiment_stats = cursor.fetchall()
    
    print("\n뉴스 감성별 성능:")
    for sentiment in sentiment_stats:
        win_rate = (sentiment['wins'] / sentiment['count'] * 100) if sentiment['count'] > 0 else 0
        print(f"{sentiment['sentiment_range']}: {sentiment['count']}거래, {sentiment['wins']}승, 승률 {win_rate:.2f}%")
    
    # 시간대별 성능 (일중)
    cursor.execute('''
    SELECT 
        SUBSTR(timestamp, 12, 2) as hour,
        COUNT(*) as count,
        (SELECT COUNT(*) FROM trades t JOIN trade_results tr ON t.id = tr.trade_id 
         WHERE SUBSTR(t.timestamp, 12, 2) = hour AND tr.profit_loss > 0) as wins
    FROM trades
    WHERE status = 'closed'
    GROUP BY hour
    ORDER BY hour
    ''')
    hourly_stats = cursor.fetchall()
    
    print("\n시간대별 성능:")
    for hour in hourly_stats:
        win_rate = (hour['wins'] / hour['count'] * 100) if hour['count'] > 0 else 0
        print(f"{hour['hour']}시: {hour['count']}거래, {hour['wins']}승, 승률 {win_rate:.2f}%")
    
    # 최근 10개 거래 상세 정보
    cursor.execute('''
    SELECT t.*, tr.close_timestamp, tr.close_price, tr.profit_loss, tr.profit_loss_percentage, tr.result_type
    FROM trades t
    LEFT JOIN trade_results tr ON t.id = tr.trade_id
    ORDER BY t.timestamp DESC
    LIMIT 10
    ''')
    recent_trades = cursor.fetchall()
    
    print("\n최근 10개 거래 상세:")
    for i, trade in enumerate(recent_trades, 1):
        direction = '롱' if trade['direction'] == 'long' else '숏'
        status = '종료' if trade['status'] == 'closed' else '진행 중'
        print(f"\n{i}. 거래 ID: {trade['id']} ({status})")
        print(f"시작: {trade['timestamp']} / 방향: {direction} / 레버리지: {trade['leverage']}x")
        print(f"진입가격: ${trade['entry_price']:.2f} / 수량: {trade['amount']:.4f} BTC")
        print(f"포지션 크기: 자본의 {trade['position_size']*100:.1f}% (${trade['investment_amount']:.2f})")
        print(f"스탑로스: ${trade['stop_loss_price']:.2f} ({trade['stop_loss_percentage']}%)")
        print(f"테이크프로핏: ${trade['take_profit_price']:.2f} ({trade['take_profit_percentage']}%)")
        print(f"승률 예측: {trade['probability']:.2f} / 감성 점수: {trade['sentiment_score']}")
        
        if trade['status'] == 'closed':
            result = '이익' if trade['profit_loss'] > 0 else '손실'
            result_type = '익절' if trade['result_type'] == 'take_profit' else '손절'
            print(f"종료: {trade['close_timestamp']} / 종료가격: ${trade['close_price']:.2f}")
            print(f"결과: {result} ${trade['profit_loss']:.2f} ({trade['profit_loss_percentage']:.2f}%) - {result_type}")
    
    print("\n============================================================")
    
    conn.close()

def start_trading_bot():
    """
    비트코인 자동 거래 봇의 메인 함수
    - 멀티 타임프레임 분석
    - 뉴스 감성 분석
    - 자가 학습 최적화
    - 자동 거래 실행
    """
    print("\n=== Bitcoin Trading Bot with Self-Learning Capabilities Started ===")
    print(f"시작 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("거래 페어:", SYMBOL)
    print("주요 기능:")
    print("- 멀티 타임프레임 분석 (15분, 4시간, 1일)")
    print("- 뉴스 감성 분석")
    print("- 켈리 기준 기반 포지션 사이징")
    print("- 동적 레버리지 최적화 (1x-10x)")
    print("- 동적 SL/TP 최적화")
    print("- 과거 거래 기반 자가 학습")
    print("- SQLite 데이터베이스 거래 추적")
    print("======================================================\n")
    
    # 데이터베이스 초기화
    init_database()
    
    # 거래 통계 출력 (있다면)
    stats = print_trade_stats()
    
    # 충분한 거래 내역이 있는 경우 AI 학습 결과 출력
    if stats['total_trades'] >= 5:
        print("\n=== AI 자가 학습 인사이트 ===")
        historical_data = get_historical_trades_analysis()
        
        # 성공적인 거래 패턴 출력
        if 'pattern_insights' in historical_data and 'winning_patterns' in historical_data['pattern_insights']:
            print("가장 성공적인 패턴:")
            for i, pattern in enumerate(historical_data['pattern_insights']['winning_patterns'][:3], 1):
                direction = "롱" if pattern['direction'] == "long" else "숏"
                sentiment = pattern['sentiment_category'].replace("very_", "매우 ").replace("negative", "부정적").replace("positive", "긍정적").replace("neutral", "중립")
                print(f"{i}. {direction} + {pattern['leverage']}x + {sentiment} 뉴스 = {pattern['avg_profit']:.2f}% 평균 수익 ({pattern['count']}회)")
        
        # 실패한 거래 패턴 출력
        if 'pattern_insights' in historical_data and 'losing_patterns' in historical_data['pattern_insights']:
            print("\n가장 실패한 패턴:")
            for i, pattern in enumerate(historical_data['pattern_insights']['losing_patterns'][:3], 1):
                direction = "롱" if pattern['direction'] == "long" else "숏"
                sentiment = pattern['sentiment_category'].replace("very_", "매우 ").replace("negative", "부정적").replace("positive", "긍정적").replace("neutral", "중립")
                print(f"{i}. {direction} + {pattern['leverage']}x + {sentiment} 뉴스 = {pattern['avg_profit']:.2f}% 평균 손실 ({pattern['count']}회)")
        
        print("===============================\n")
    
    # 메인 루프
    while True:
        try:
            # 현재 시간 및 가격 조회
            current_time = datetime.now().strftime('%H:%M:%S')
            current_price = exchange.fetch_ticker(SYMBOL)['last']
            print(f"\n[{current_time}] 현재 BTC 가격: ${current_price:,.2f}")
            
            # 포지션 확인 및 DB 업데이트
            current_side, amount = check_and_update_positions(exchange, SYMBOL)
            
            # 이미 포지션이 있으면 모니터링만 하고 다음 루프로
            if current_side:
                print(f"현재 포지션: {current_side.upper()} {amount} BTC")
                time.sleep(60)  # 포지션이 있으면 1분마다 체크
                continue
            
            # 포지션이 없을 경우 분석 시작
            print("포지션 없음. 시장 분석 시작...")
            
            # 미체결 주문 취소
            cancel_all_orders(exchange, SYMBOL)
            time.sleep(2)
            
            # 차트 데이터 수집
            df_15m, df_4h, df_1d = fetch_ohlcv_data(exchange, SYMBOL)
            
            # 통계 계산
            stats_15m = calculate_stats(df_15m)
            stats_4h = calculate_stats(df_4h)
            stats_1d = calculate_stats(df_1d)
            
            # 데이터 분석 준비
            analysis_data = prepare_analysis_data(df_15m, df_4h, df_1d, stats_15m, stats_4h, stats_1d, current_price)
            
            # 비트코인 뉴스 검색
            print("최신 비트코인 뉴스 가져오는 중...")
            bitcoin_news = fetch_bitcoin_news(client)
            print("\n=== 최신 비트코인 뉴스 ===")
            print(bitcoin_news)
            
            # 뉴스 감성 분석
            sentiment_score = analyze_news_sentiment(client, bitcoin_news)
            print(f"뉴스 감성 점수: {sentiment_score} (-10 ~ +10 스케일)")
            print("===========================\n")
            
            # 뉴스 정보 저장
            save_news_to_db(bitcoin_news, sentiment_score)
            
            # 자가 학습 기능이 포함된 완전 동적 최적화 분석 실행
            print("자가 학습 기반 동적 최적화 분석 중...")
            direction, probability, position_size, leverage, sl_percentage, tp_percentage, reasoning = analyze_with_dynamic_optimization(
                analysis_data, bitcoin_news, sentiment_score
            )
            
            # 분석 결과 출력
            print(f"AI 결정: {direction.upper()} / 자본의 {position_size:.1%} 투자")
            print(f"예상 승률: {probability:.2f}")
            print(f"레버리지: {leverage}배")
            print(f"손절: {sl_percentage:.2f}%")
            print(f"익절: {tp_percentage:.2f}%")
            if sl_percentage > 0:
                print(f"위험:보상 비율: 1:{tp_percentage/sl_percentage:.2f}")
            print(f"분석 이유: {reasoning}")
            
            # 트레이딩 로직 실행
            if direction == "wait" or position_size <= 0 or leverage <= 0:
                print("AI가 대기를 추천합니다. 포지션 진입하지 않음.")
                time.sleep(300)  # 5분 대기 후 재분석
                continue
            
            # 주문 실행
            success, trade_id = execute_order(
                exchange, SYMBOL, direction, position_size, leverage, 
                sl_percentage, tp_percentage, current_price, probability, sentiment_score
            )
            
            if not success:
                print("주문 실행 실패. 잠시 후 재시도...")
                time.sleep(60)
            else:
                print(f"주문 성공적으로 실행됨. 거래 ID: {trade_id}")
            
            # 다음 분석 전 대기
            time.sleep(60)
            
        except Exception as e:
            print(f"\n메인 루프 오류: {e}")
            time.sleep(60)  # 오류 발생 시 1분 대기 후 재시도

# ==========================================================
# 9. 프로그램 진입점
# ==========================================================

if __name__ == "__main__":
    # 명령행 인수 처리
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "--summary":
        # 요약 출력 모드
        show_summary()
    else:
        # 트레이딩 봇 실행 모드
        start_trading_bot()