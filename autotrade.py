import os
import math
import time
import pandas as pd
import json
import re
import sqlite3
from datetime import datetime, timedelta

try:
    import ccxt
    from dotenv import load_dotenv
    from openai import OpenAI
except ImportError:
    print("필요한 패키지가 설치되어 있지 않습니다. 다음 명령어로 설치하세요:")
    print("pip install ccxt python-dotenv openai pandas")
    exit(1)

# 여기에 전역 변수 및 환경 변수 설정 코드를 추가합니다
# 환경 변수 로드
load_dotenv()

# 거래 설정
symbol = "BTC/USDT"  # 거래 페어 설정 
MAX_POSITION_SIZE = 0.2  # 최대 자본의 20% 투자
MIN_LEVERAGE = 1  # 최소 레버리지
MAX_LEVERAGE = 10  # 최대 레버리지

# 바이낸스 API 설정
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_SECRET_KEY")

# OpenAI API 설정
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# 데이터베이스 설정
DB_FILE = 'bitcoin_trades.db'

# CCXT 거래소 초기화
try:
    exchange = ccxt.binance({
        'apiKey': API_KEY,
        'secret': API_SECRET,
        'enableRateLimit': True,
        'options': {
            'defaultType': 'future',  # 선물 거래 사용
        }
    })
    print("거래소 연결 성공")
except Exception as e:
    print(f"거래소 연결 실패: {e}")
    exchange = None

# OpenAI 클라이언트 초기화
try:
    client = OpenAI(api_key=OPENAI_API_KEY)
    print("OpenAI API 연결 성공")
except Exception as e:
    print(f"OpenAI API 연결 실패: {e}")
    client = None

def get_historical_trades_analysis():
    """
    데이터베이스에서 과거 거래 내역을 분석하여 AI 판단을 위한 데이터를 제공
    
    Returns:
        dict: 거래 분석 데이터 (성공/실패 패턴, 레버리지별 성과, 타임프레임별 성과 등)
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

# 거래 결과 요약 출력 함수
def show_summary():
    """모든 거래 내역과 통계를 출력"""
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

# 프로그램 실행
if __name__ == "__main__":
    # 명령행 인수 처리
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "--summary":
        # 요약 출력 모드
        show_summary()    
        

def start_trading_bot():
    print("\n=== Bitcoin Trading Bot with Full Dynamic Optimization Started ===")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("Trading Pair:", symbol)
    print("Features:")
    print("- Multi-timeframe analysis (15m, 4h, 1d)")
    print("- News Sentiment Analysis")
    print("- Kelly Criterion for position sizing")
    print("- Dynamic Leverage Optimization (1x-10x)")
    print("- Dynamic SL/TP Optimization")
    print("- SQLite Database for trade tracking")
    print("======================================================\n")
    
    # 데이터베이스 초기화
    init_database()
    
    # 거래 통계 출력 (있다면)
    print_trade_stats()
    
    # 메인 루프
    while True:
        try:
            # 현재 시간 및 가격 조회
            current_time = datetime.now().strftime('%H:%M:%S')
            current_price = exchange.fetch_ticker(symbol)['last']
            print(f"\n[{current_time}] Current BTC Price: ${current_price:,.2f}")
            
            # 포지션 확인 및 DB 업데이트
            current_side, amount = check_and_update_positions(exchange, symbol)
            
            if current_side:
                print(f"Current Position: {current_side.upper()} {amount} BTC")
                time.sleep(60)  # 포지션이 있으면 1분마다 체크
                continue
            
            # 포지션이 없을 경우 분석 시작
            print("No position. Starting market analysis...")
            
            # 미체결 주문 취소
            cancel_all_orders(exchange, symbol)
            time.sleep(2)
            
            # 차트 데이터 수집
            df_15m, df_4h, df_1d = fetch_ohlcv_data(exchange, symbol)
            
            # 통계 계산
            stats_15m = calculate_stats(df_15m)
            stats_4h = calculate_stats(df_4h)
            stats_1d = calculate_stats(df_1d)
            
            # 데이터 분석 준비
            analysis_data = prepare_analysis_data(df_15m, df_4h, df_1d, stats_15m, stats_4h, stats_1d, current_price)
            
            # 비트코인 뉴스 검색
            print("Fetching latest Bitcoin news...")
            bitcoin_news = fetch_bitcoin_news(client)
            print("\n=== Latest Bitcoin News ===")
            print(bitcoin_news)
            
            # 뉴스 감성 분석
            sentiment_score = analyze_news_sentiment(client, bitcoin_news)
            print(f"News Sentiment Score: {sentiment_score} (-10 to +10 scale)")
            print("===========================\n")
            
            # 뉴스 정보 저장
            save_news_to_db(bitcoin_news, sentiment_score)
            
            # 완전 동적 최적화 분석 실행
            print("Analyzing market data with full dynamic optimization...")
            direction, probability, position_size, leverage, sl_percentage, tp_percentage, reasoning = analyze_with_dynamic_optimization(
                analysis_data, bitcoin_news, sentiment_score
            )
            
            print(f"AI Decision: {direction.upper()} with {position_size:.1%} of capital")
            print(f"Win Probability: {probability:.2f}")
            print(f"Leverage: {leverage}x")
            print(f"Stop Loss: {sl_percentage:.2f}%")
            print(f"Take Profit: {tp_percentage:.2f}%")
            if sl_percentage > 0:
                print(f"Risk:Reward Ratio: 1:{tp_percentage/sl_percentage:.2f}")
            print(f"Reasoning: {reasoning}")
            
            # 트레이딩 로직
            if direction == "wait" or position_size <= 0 or leverage <= 0:
                print("AI suggests to WAIT. No trade will be executed.")
                time.sleep(300)  # 5분 대기 후 재분석
                continue
            
            # 주문 실행
            success, trade_id = execute_order(
                exchange, symbol, direction, position_size, leverage, 
                sl_percentage, tp_percentage, current_price, probability, sentiment_score
            )
            if not success:
                print("Order execution failed. Waiting before retry...")
                time.sleep(60)
            else:
                print(f"Order successfully executed with trade ID: {trade_id}")
            
            # 다음 분석 전 대기
            time.sleep(60)
            
        except Exception as e:
            print(f"\nError in main loop: {e}")
            time.sleep(60)  # 오류 발생 시 1분 대기 후 재시도# 거래 통계 출력 함수
def print_trade_stats():
    """거래 통계를 출력"""
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
    
    return stats# 데이터 전처리 함수
def prepare_analysis_data(df_15m, df_4h, df_1d, stats_15m, stats_4h, stats_1d, current_price):
    """차트 데이터를 분석 가능한 형태로 변환"""
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
            '15m': stats_15m,
            '4h': stats_4h,
            '1d': stats_1d
        },
        'current_price': current_price
    }# 거래 통계 출력 함수
def print_trade_stats():
    """거래 통계를 출력"""
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
"""
비트코인 멀티 타임프레임 및 뉴스 감성 분석 트레이딩 봇
- 멀티 타임프레임 분석: 15분, 4시간, 1일
- 뉴스 감성 분석: OpenAI Web Search API 활용
- 바이낸스 퓨처스 연동
- 켈리 기준(Kelly Criterion)에 따른 동적 포지션 사이징
- 동적 레버리지 최적화 (1x-10x)
- 동적 SL/TP 설정
- SQLite 데이터베이스 거래 내역 저장
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
    import ccxt
    from dotenv import load_dotenv
    from openai import OpenAI
except ImportError:
    print("필요한 패키지가 설치되어 있지 않습니다. 다음 명령어로 설치하세요:")
    print("pip install ccxt python-dotenv openai pandas")
    exit(1)

# 환경 변수 로드
load_dotenv()

# 데이터베이스 초기화 및 설정
DB_FILE = 'bitcoin_trades.db'

def init_database():
    """SQLite 데이터베이스 초기화 및 테이블 생성"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # 거래 내역 테이블 생성
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        direction TEXT NOT NULL,
        entry_price REAL NOT NULL,
        amount REAL NOT NULL,
        leverage INTEGER NOT NULL,
        position_size REAL NOT NULL,
        investment_amount REAL NOT NULL,
        stop_loss_price REAL NOT NULL,
        take_profit_price REAL NOT NULL,
        stop_loss_percentage REAL NOT NULL,
        take_profit_percentage REAL NOT NULL,
        risk_reward_ratio REAL NOT NULL,
        probability REAL NOT NULL,
        sentiment_score INTEGER,
        status TEXT DEFAULT 'open'
    )
    ''')
    
    # 뉴스 내역 테이블 생성
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS news (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        news_content TEXT NOT NULL,
        sentiment_score INTEGER NOT NULL
    )
    ''')
    
    # 거래 결과 테이블 생성
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS trade_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_id INTEGER NOT NULL,
        close_timestamp TEXT NOT NULL,
        close_price REAL NOT NULL,
        profit_loss REAL NOT NULL,
        profit_loss_percentage REAL NOT NULL,
        result_type TEXT NOT NULL,
        FOREIGN KEY (trade_id) REFERENCES trades(id)
    )
    ''')
    
    conn.commit()
    conn.close()
    print(f"데이터베이스 초기화 완료: {DB_FILE}")

def save_trade_to_db(direction, entry_price, amount, leverage, position_size, investment_amount, 
                     sl_price, tp_price, sl_percentage, tp_percentage, risk_reward_ratio, probability, sentiment_score):
    """거래 내역을 데이터베이스에 저장"""
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
    """뉴스 및 감성 점수를 데이터베이스에 저장"""
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
    """거래 결과 업데이트"""
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
        profit_loss = (close_price - entry_price) * amount
        profit_loss_percentage = ((close_price / entry_price) - 1) * 100
    else:  # short
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
    """현재 열려 있는 모든 거래 가져오기"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM trades WHERE status = 'open'")
    open_trades = cursor.fetchall()
    
    conn.close()
    return open_trades

def get_trade_history(limit=10):
    """최근 거래 내역 가져오기"""
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
    """거래 통계 정보 가져오기"""
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

# RSI 계산 함수
def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

# 최신 뉴스 가져오기 함수
def fetch_bitcoin_news(client):
    try:
        current_date = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        
        news_response = client.responses.create(
            model="gpt-4.1",  # Web Search 지원 모델
            tools=[{
                "type": "web_search_preview", 
                "search_context_size": "medium"
            }],
            input=f"What are the most significant Bitcoin news from {yesterday} to {current_date}? Focus on price movements, regulations, institutional adoption, and market sentiment. List only the 3-5 most impactful headlines with brief factual summaries."
        )
        
        return news_response.output_text
    
    except Exception as e:
        print(f"Error fetching news: {e}")
        return "Unable to fetch recent Bitcoin news due to an error."

# 뉴스 감성 분석 함수
def analyze_news_sentiment(client, news_text):
    try:
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
        # 숫자만 추출
        score_match = re.search(r'(-?\d+)', sentiment_score)
        if score_match:
            return int(score_match.group(1))
        return 0  # 중립으로 기본 설정
        
    except Exception as e:
        print(f"Error analyzing news sentiment: {e}")
        return 0  # 오류 시 중립으로 반환

# 차트 데이터 수집 함수
def fetch_ohlcv_data(exchange, symbol):
    # 15분봉 데이터 (단기)
    ohlcv_15m = exchange.fetch_ohlcv(symbol, timeframe="15m", limit=96)
    df_15m = pd.DataFrame(ohlcv_15m, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df_15m['timestamp'] = pd.to_datetime(df_15m['timestamp'], unit='ms')
    
    # 4시간봉 데이터 (중기)
    ohlcv_4h = exchange.fetch_ohlcv(symbol, timeframe="4h", limit=48)
    df_4h = pd.DataFrame(ohlcv_4h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df_4h['timestamp'] = pd.to_datetime(df_4h['timestamp'], unit='ms')
    
    # 일봉 데이터 (장기)
    ohlcv_1d = exchange.fetch_ohlcv(symbol, timeframe="1d", limit=30)
    df_1d = pd.DataFrame(ohlcv_1d, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df_1d['timestamp'] = pd.to_datetime(df_1d['timestamp'], unit='ms')
    
    return df_15m, df_4h, df_1d

# 통계 계산 함수
def calculate_stats(df):
    stats = {}
    stats['last_close'] = df['close'].iloc[-1]
    stats['avg_5'] = df['close'].rolling(5).mean().iloc[-1]
    stats['avg_10'] = df['close'].rolling(10).mean().iloc[-1]
    stats['avg_20'] = df['close'].rolling(20).mean().iloc[-1]
    stats['vol_avg'] = df['volume'].mean()
    stats['last_vol'] = df['volume'].iloc[-1]
    stats['highest_high'] = df['high'].max()
    stats['lowest_low'] = df['low'].min()
    # 추가적인 통계 (모멘텀 지표)
    stats['rsi_14'] = calculate_rsi(df['close'], 14).iloc[-1] if len(df) > 14 else 50
    return stats

# 포지션 취소 및 주문 생성 함수
def cancel_all_orders(exchange, symbol):
    try:
        open_orders = exchange.fetch_open_orders(symbol)
        if open_orders:
            for order in open_orders:
                exchange.cancel_order(order['id'], symbol)
            print("Cancelled all open orders for", symbol)
            return True
        else:
            print("No open orders to cancel.")
            return False
    except Exception as e:
        print(f"Error cancelling orders: {e}")
        return False

# 포지션 확인 함수
def check_position(exchange, symbol):
    """거래소에서 현재 포지션 정보를 가져옴
    
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
        print(f"Error checking position: {e}")
        # 오류 발생 시 안전하게 None, 0 반환
        return None, 0

# 포지션 체크 및 DB 업데이트 함수
def check_and_update_positions(exchange, symbol):
    """포지션 상태를 확인하고 닫힌 포지션을 DB에 업데이트"""
    # DB에서 열려있는 거래 가져오기
    open_trades = get_open_trades()
    
    # 현재 포지션 정보
    current_side, current_amount = check_position(exchange, symbol)
    
    if not open_trades:
        return current_side, current_amount  # 적절한 값을 반환
    
    current_price = exchange.fetch_ticker(symbol)['last']
    
    # 포지션이 없는데 DB에 열린 거래가 있다면, 포지션이 닫혔다는 의미
    if not current_side and open_trades:
        for trade in open_trades:
            trade_id = trade[0]  # id는 첫 번째 컬럼
            direction = trade[2]  # direction은 세 번째 컬럼
            
            # 포지션이 SL 또는 TP에 도달하여 닫힌 경우
            sl_price = trade[8]  # stop_loss_price
            tp_price = trade[9]  # take_profit_price
            
            # 가격에 따라 SL 또는 TP 결정 (실제로는 더 정확한 방법 필요)
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

# 완전 동적 분석 함수 (과거 거래 데이터 활용 기능 추가)
def analyze_with_dynamic_optimization(analysis_data, bitcoin_news, sentiment_score):
    """
    켈리 기준, 레버리지, SL/TP를 동적으로 최적화하고 과거 거래 데이터를 활용하여 
    자가 학습 기능을 탑재한 분석 함수
    
    Parameters:
    - analysis_data: 멀티 타임프레임 기술적 분석 데이터
    - bitcoin_news: 최신 비트코인 뉴스
    - sentiment_score: 뉴스 감성 점수 (-10 ~ +10)
    
    Returns:
    - direction: 'long', 'short', 또는 'wait' 트레이딩 방향
    - probability: 승리 확률 (0.0 ~ 1.0)
    - position_size: 투자 비율 (0.0 ~ 1.0)
    - leverage: 레버리지 배수 (1 ~ 10)
    - sl_percentage: 스탑로스 비율 (%)
    - tp_percentage: 테이크프로핏 비율 (%)
    - reasoning: 결정에 대한 설명
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

    direction = "wait"
    probability = 0.5
    position_size = 0
    leverage = 0
    sl_percentage = 0
    tp_percentage = 0
    reasoning = ""

    for line in lines:
        line = line.strip()
        if line.startswith("DIRECTION:"):
            direction = line.replace("DIRECTION:", "").strip().lower()
        elif line.startswith("PROBABILITY:"):
            try:
                probability = float(line.replace("PROBABILITY:", "").strip())
            except ValueError:
                print("Warning: Could not parse probability value")
        elif line.startswith("POSITION_SIZE:"):
            try:
                position_size_str = line.replace("POSITION_SIZE:", "").strip().replace("%", "")
                position_size = float(position_size_str) / 100
            except ValueError:
                print("Warning: Could not parse position size value")
        elif line.startswith("LEVERAGE:"):
            try:
                leverage_str = line.replace("LEVERAGE:", "").strip().replace("x", "")
                leverage = int(float(leverage_str))
            except ValueError:
                print("Warning: Could not parse leverage value")
        elif line.startswith("STOP_LOSS:"):
            try:
                sl_str = line.replace("STOP_LOSS:", "").strip().replace("%", "")
                sl_percentage = float(sl_str)
            except ValueError:
                print("Warning: Could not parse stop loss value")
        elif line.startswith("TAKE_PROFIT:"):
            try:
                tp_str = line.replace("TAKE_PROFIT:", "").strip().replace("%", "")
                tp_percentage = float(tp_str)
            except ValueError:
                print("Warning: Could not parse take profit value")
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
    
    # 최소 승률 조건 확인
    if probability < 0.55:
        direction = "wait"
        position_size = 0
        leverage = 0
        sl_percentage = 0
        tp_percentage = 0
    
    # 거래 내역에 기반하여 추가 검증 (최소 5건 이상의 거래 내역이 있을 때)
    if historical_trades['overall_stats']['total_trades'] >= 5:
        # 현재 감성 점수와 유사한 상황에서의 과거 성과 확인
        sentiment_performance = None
        for perf in historical_trades['sentiment_performance']:
            if perf['sentiment_score'] == sentiment_score:
                sentiment_performance = perf
                break
        
        # 비슷한 감성 점수에서 승률이 30% 미만이었다면 더 신중하게 접근
        if sentiment_performance and sentiment_performance['win_rate'] < 30:
            print(f"Warning: Poor historical performance with sentiment score {sentiment_score} (win rate: {sentiment_performance['win_rate']:.2f}%)")
            
            # 승률 조건 상향 조정 (더 확실한 신호에만 진입)
            if probability < 0.65:
                direction = "wait"
                position_size = 0
                leverage = 0
                sl_percentage = 0
                tp_percentage = 0
                reasoning += "\n(Historical data shows poor performance with similar sentiment scores. Trade avoided due to higher probability threshold.)"
        
        # 과거에 실패했던 레버리지 피하기
        leverage_to_avoid = None
        for perf in historical_trades['leverage_performance']:
            if perf['leverage'] == leverage and perf['win_rate'] < 30 and perf['count'] >= 3:
                leverage_to_avoid = leverage
                break
        
        if leverage_to_avoid:
            print(f"Warning: Poor historical performance with leverage {leverage_to_avoid}x (win rate: <30%)")
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
                reasoning += f"\n(Adjusted leverage to {leverage}x based on historical performance data)"
    
    return direction, probability, position_size, leverage, sl_percentage, tp_percentage, reasoning




# 주문 실행 함수
def execute_order(exchange, symbol, action, position_size, leverage, sl_percentage, tp_percentage, current_price, probability, sentiment_score):
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
        amount = math.floor(amount * 1000) / 1000  # 소수점 3자리까지 내림
        
        if action == "long":
            # 롱 포지션 진입
            order = exchange.create_market_buy_order(symbol, amount)
            entry_price = current_price
            sl_price = round(entry_price * (1 - sl_percentage/100), 2)
            tp_price = round(entry_price * (1 + tp_percentage/100), 2)
            
            # SL/TP 주문 생성
            exchange.create_order(symbol, 'STOP_MARKET', 'sell', amount, None, {'stopPrice': sl_price})
            exchange.create_order(symbol, 'TAKE_PROFIT_MARKET', 'sell', amount, None, {'stopPrice': tp_price})
            
            # 거래 정보 출력
            risk_reward_ratio = tp_percentage/sl_percentage
            print(f"\n=== LONG Position Opened ===")
            print(f"Position Size: {position_size:.1%} of capital (${investment_amount:.2f})")
            print(f"Leverage: {leverage}x")
            print(f"BTC Amount: {amount:.3f} BTC")
            print(f"Entry: ${entry_price:,.2f}")
            print(f"Stop Loss: ${sl_price:,.2f} (-{sl_percentage}%)")
            print(f"Take Profit: ${tp_price:,.2f} (+{tp_percentage}%)")
            print(f"Risk:Reward Ratio: 1:{risk_reward_ratio:.2f}")
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
            # 숏 포지션 진입
            order = exchange.create_market_sell_order(symbol, amount)
            entry_price = current_price
            sl_price = round(entry_price * (1 + sl_percentage/100), 2)
            tp_price = round(entry_price * (1 - tp_percentage/100), 2)
            
            # SL/TP 주문 생성
            exchange.create_order(symbol, 'STOP_MARKET', 'buy', amount, None, {'stopPrice': sl_price})
            exchange.create_order(symbol, 'TAKE_PROFIT_MARKET', 'buy', amount, None, {'stopPrice': tp_price})
            
            # 거래 정보 출력
            risk_reward_ratio = tp_percentage/sl_percentage
            print(f"\n=== SHORT Position Opened ===")
            print(f"Position Size: {position_size:.1%} of capital (${investment_amount:.2f})")
            print(f"Leverage: {leverage}x")
            print(f"BTC Amount: {amount:.3f} BTC")
            print(f"Entry: ${entry_price:,.2f}")
            print(f"Stop Loss: ${sl_price:,.2f} (+{sl_percentage}%)")
            print(f"Take Profit: ${tp_price:,.2f} (-{tp_percentage}%)")
            print(f"Risk:Reward Ratio: 1:{risk_reward_ratio:.2f}")
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
        print(f"Error executing order: {e}")
        return False, None

# 데이터 전처리 함수
def prepare_analysis_data(df_15m, df_4h, df_1d, stats_15m, stats_4h, stats_1d, current_price):
    """차트 데이터를 분석 가능한 형태로 변환"""
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
            '15m': stats_15m,
            '4h': stats_4h,
            '1d': stats_1d
        },
        'current_price': current_price
    }

# 메인 트레이딩 함수 (일부 수정)
def start_trading_bot():
    print("\n=== Bitcoin Trading Bot with Self-Learning Capabilities Started ===")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("Trading Pair:", symbol)
    print("Features:")
    print("- Multi-timeframe analysis (15m, 4h, 1d)")
    print("- News Sentiment Analysis")
    print("- Kelly Criterion for position sizing")
    print("- Dynamic Leverage Optimization (1x-10x)")
    print("- Dynamic SL/TP Optimization")
    print("- Self-Learning from Historical Trades")
    print("- SQLite Database for trade tracking")
    print("======================================================\n")
    
    # 데이터베이스 초기화
    init_database()
    
    # 거래 통계 출력 (있다면)
    stats = print_trade_stats()
    
    # 충분한 거래 내역이 있는 경우 AI 학습 결과 출력
    if stats['total_trades'] >= 5:
        print("\n=== AI Self-Learning Insights ===")
        historical_data = get_historical_trades_analysis()
        
        # 성공적인 거래 패턴 출력
        if 'pattern_insights' in historical_data and 'winning_patterns' in historical_data['pattern_insights']:
            print("Top Winning Patterns:")
            for i, pattern in enumerate(historical_data['pattern_insights']['winning_patterns'][:3], 1):
                direction = "롱" if pattern['direction'] == "long" else "숏"
                sentiment = pattern['sentiment_category'].replace("very_", "매우 ").replace("negative", "부정적").replace("positive", "긍정적").replace("neutral", "중립")
                print(f"{i}. {direction} + {pattern['leverage']}x + {sentiment} 뉴스 = {pattern['avg_profit']:.2f}% 평균 수익 ({pattern['count']}회)")
        
        # 실패한 거래 패턴 출력
        if 'pattern_insights' in historical_data and 'losing_patterns' in historical_data['pattern_insights']:
            print("\nTop Losing Patterns:")
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
            current_price = exchange.fetch_ticker(symbol)['last']
            print(f"\n[{current_time}] Current BTC Price: ${current_price:,.2f}")
            
            # 포지션 확인 및 DB 업데이트
            current_side, amount = check_and_update_positions(exchange, symbol)
            
            if current_side:
                print(f"Current Position: {current_side.upper()} {amount} BTC")
                time.sleep(60)  # 포지션이 있으면 1분마다 체크
                continue
            
            # 포지션이 없을 경우 분석 시작
            print("No position. Starting market analysis with self-learning...")
            
            # 미체결 주문 취소
            cancel_all_orders(exchange, symbol)
            time.sleep(2)
            
            # 차트 데이터 수집
            df_15m, df_4h, df_1d = fetch_ohlcv_data(exchange, symbol)
            
            # 통계 계산
            stats_15m = calculate_stats(df_15m)
            stats_4h = calculate_stats(df_4h)
            stats_1d = calculate_stats(df_1d)
            
            # 데이터 분석 준비
            analysis_data = prepare_analysis_data(df_15m, df_4h, df_1d, stats_15m, stats_4h, stats_1d, current_price)
            
            # 비트코인 뉴스 검색
            print("Fetching latest Bitcoin news...")
            bitcoin_news = fetch_bitcoin_news(client)
            print("\n=== Latest Bitcoin News ===")
            print(bitcoin_news)
            
            # 뉴스 감성 분석
            sentiment_score = analyze_news_sentiment(client, bitcoin_news)
            print(f"News Sentiment Score: {sentiment_score} (-10 to +10 scale)")
            print("===========================\n")
            
            # 뉴스 정보 저장
            save_news_to_db(bitcoin_news, sentiment_score)
            
            # 자가 학습 기능이 포함된 완전 동적 최적화 분석 실행
            print("Analyzing market data with self-learning dynamic optimization...")
            direction, probability, position_size, leverage, sl_percentage, tp_percentage, reasoning = analyze_with_dynamic_optimization(
                analysis_data, bitcoin_news, sentiment_score
            )
            
            print(f"AI Decision: {direction.upper()} with {position_size:.1%} of capital")
            print(f"Win Probability: {probability:.2f}")
            print(f"Leverage: {leverage}x")
            print(f"Stop Loss: {sl_percentage:.2f}%")
            print(f"Take Profit: {tp_percentage:.2f}%")
            if sl_percentage > 0:
                print(f"Risk:Reward Ratio: 1:{tp_percentage/sl_percentage:.2f}")
            print(f"Reasoning: {reasoning}")
            
            # 트레이딩 로직
            if direction == "wait" or position_size <= 0 or leverage <= 0:
                print("AI suggests to WAIT. No trade will be executed.")
                time.sleep(300)  # 5분 대기 후 재분석
                continue
            
            # 주문 실행
            success, trade_id = execute_order(
                exchange, symbol, direction, position_size, leverage, 
                sl_percentage, tp_percentage, current_price, probability, sentiment_score
            )
            if not success:
                print("Order execution failed. Waiting before retry...")
                time.sleep(60)
            else:
                print(f"Order successfully executed with trade ID: {trade_id}")
            
            # 다음 분석 전 대기
            time.sleep(60)
            
        except Exception as e:
            print(f"\nError in main loop: {e}")
            time.sleep(60)  # 오류 발생 시 1분 대기 후 재시도

# 거래 결과 요약 출력 함수
def show_summary():
    """모든 거래 내역과 통계를 출력"""
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

# 프로그램 실행
if __name__ == "__main__":
    # 명령행 인수 처리
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "--summary":
        # 요약 출력 모드
        show_summary()
    else:
        # 트레이딩 봇 실행 모드
        start_trading_bot()