import streamlit as st
import pandas as pd
import numpy as np
import sqlite3
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import datetime
from datetime import timedelta

# 페이지 설정입니다
st.set_page_config(
    page_title="Bitcoin Trading Bot Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 사이드바 제목
st.sidebar.title("📈 Bitcoin Trading Bot Dashboard")
st.sidebar.markdown("---")

# 데이터베이스 연결 함수
@st.cache_resource
def get_connection():
    return sqlite3.connect('bitcoin_trades.db')

# 데이터 로드 함수
@st.cache_data(ttl=300)  # 5분마다 갱신
def load_trades_data():
    conn = get_connection()
    
    # 모든 거래 데이터 (종료된 거래만)
    query = """
    SELECT t.*, tr.close_timestamp, tr.close_price, tr.profit_loss, tr.profit_loss_percentage, tr.result_type
    FROM trades t
    JOIN trade_results tr ON t.id = tr.trade_id
    WHERE t.status = 'closed'
    ORDER BY t.timestamp DESC
    """
    
    trades_df = pd.read_sql_query(query, conn)
    
    # 날짜 열 변환
    trades_df['timestamp'] = pd.to_datetime(trades_df['timestamp'])
    trades_df['close_timestamp'] = pd.to_datetime(trades_df['close_timestamp'])
    
    # 보유 기간 계산
    trades_df['hold_duration'] = (trades_df['close_timestamp'] - trades_df['timestamp']).dt.total_seconds() / 60  # 분 단위
    
    return trades_df

@st.cache_data(ttl=300)
def load_open_trades_data():
    conn = get_connection()
    
    # 열린 거래 데이터
    query = """
    SELECT *
    FROM trades
    WHERE status = 'open'
    ORDER BY timestamp DESC
    """
    
    open_trades_df = pd.read_sql_query(query, conn)
    
    if not open_trades_df.empty:
        open_trades_df['timestamp'] = pd.to_datetime(open_trades_df['timestamp'])
    
    return open_trades_df

@st.cache_data(ttl=300)
def load_news_data():
    conn = get_connection()
    
    # 뉴스 데이터
    query = """
    SELECT *
    FROM news
    ORDER BY timestamp DESC
    LIMIT 50
    """
    
    news_df = pd.read_sql_query(query, conn)
    
    if not news_df.empty:
        news_df['timestamp'] = pd.to_datetime(news_df['timestamp'])
    
    return news_df

# 거래 통계 계산 함수
def calculate_statistics(df):
    if df.empty:
        return {
            'total_trades': 0,
            'winning_trades': 0,
            'losing_trades': 0,
            'win_rate': 0,
            'avg_profit': 0,
            'avg_loss': 0,
            'max_profit': 0,
            'max_loss': 0,
            'total_profit': 0,
            'average_hold_time': 0,
            'profit_factor': 0,
            'sharpe_ratio': 0
        }
    
    # 기본 통계
    total_trades = len(df)
    winning_trades = len(df[df['profit_loss'] > 0])
    losing_trades = len(df[df['profit_loss'] <= 0])
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
    
    avg_profit = df[df['profit_loss'] > 0]['profit_loss_percentage'].mean() if winning_trades > 0 else 0
    avg_loss = df[df['profit_loss'] <= 0]['profit_loss_percentage'].mean() if losing_trades > 0 else 0
    
    max_profit = df['profit_loss_percentage'].max() if total_trades > 0 else 0
    max_loss = df['profit_loss_percentage'].min() if total_trades > 0 else 0
    
    total_profit = df['profit_loss_percentage'].sum() if total_trades > 0 else 0
    
    # 평균 보유 시간 (분)
    average_hold_time = df['hold_duration'].mean() if total_trades > 0 else 0
    
    # 수익 요소 (총 이익 / 총 손실의 절대값)
    total_gains = df[df['profit_loss'] > 0]['profit_loss'].sum() if winning_trades > 0 else 0
    total_losses = abs(df[df['profit_loss'] <= 0]['profit_loss'].sum()) if losing_trades > 0 else 0
    profit_factor = (total_gains / total_losses) if total_losses > 0 else 0
    
    # 샤프 비율 (수익률의 표준편차로 조정된 수익률)
    mean_return = df['profit_loss_percentage'].mean() if total_trades > 0 else 0
    std_return = df['profit_loss_percentage'].std() if total_trades > 0 else 1
    sharpe_ratio = (mean_return / std_return) if std_return > 0 else 0
    
    return {
        'total_trades': total_trades,
        'winning_trades': winning_trades,
        'losing_trades': losing_trades,
        'win_rate': win_rate,
        'avg_profit': avg_profit,
        'avg_loss': avg_loss,
        'max_profit': max_profit,
        'max_loss': max_loss,
        'total_profit': total_profit,
        'average_hold_time': average_hold_time,
        'profit_factor': profit_factor,
        'sharpe_ratio': sharpe_ratio
    }

# 메인 함수
def main():
    # 데이터 로드
    try:
        trades_df = load_trades_data()
        open_trades_df = load_open_trades_data()
        news_df = load_news_data()
        
        # 필터 옵션 (사이드바)
        st.sidebar.subheader("데이터 필터링")
        
        # 날짜 필터
        default_start_date = trades_df['timestamp'].min().date() if not trades_df.empty else datetime.date.today() - timedelta(days=30)
        default_end_date = datetime.date.today()
        
        start_date = st.sidebar.date_input("시작 날짜", default_start_date)
        end_date = st.sidebar.date_input("종료 날짜", default_end_date)
        
        # 방향 필터 (Long/Short)
        direction_options = ["모두", "Long", "Short"]
        selected_direction = st.sidebar.selectbox("포지션 방향", direction_options)
        
        # 레버리지 필터
        if not trades_df.empty:
            min_leverage = int(trades_df['leverage'].min())
            max_leverage = int(trades_df['leverage'].max())
            leverage_range = st.sidebar.slider("레버리지 범위", min_leverage, max_leverage, (min_leverage, max_leverage))
        else:
            leverage_range = (1, 10)
        
        # 필터 적용
        filtered_df = trades_df.copy()
        
        # 날짜 필터
        filtered_df = filtered_df[
            (filtered_df['timestamp'].dt.date >= start_date) & 
            (filtered_df['timestamp'].dt.date <= end_date)
        ]
        
        # 방향 필터
        if selected_direction != "모두":
            direction_map = {"Long": "long", "Short": "short"}
            filtered_df = filtered_df[filtered_df['direction'] == direction_map[selected_direction]]
        
        # 레버리지 필터
        filtered_df = filtered_df[
            (filtered_df['leverage'] >= leverage_range[0]) & 
            (filtered_df['leverage'] <= leverage_range[1])
        ]
        
        # 통계 계산
        stats = calculate_statistics(filtered_df)
        
        # 전체 통계 계산 (필터 없음)
        total_stats = calculate_statistics(trades_df)
        
        # 탭 메뉴
        tab1, tab2, tab3, tab4, tab5 = st.tabs(["📊 대시보드", "📝 거래 내역", "📰 뉴스 분석", "📈 실적 분석", "⚙️ 설정 최적화"])
        
        # 탭 1: 대시보드
        with tab1:
            st.title("비트코인 트레이딩 봇 대시보드")
            
            # 상단 KPI 카드
            col1, col2, col3, col4 = st.columns(4)
            
            with col1:
                st.metric("승률", f"{stats['win_rate']:.2f}%", f"{stats['win_rate'] - total_stats['win_rate']:.2f}%")
            
            with col2:
                st.metric("총 수익률", f"{stats['total_profit']:.2f}%", f"{stats['total_profit'] - total_stats['total_profit']:.2f}%")
            
            with col3:
                st.metric("거래 횟수", f"{stats['total_trades']}", f"{stats['total_trades'] - total_stats['total_trades']}")
            
            with col4:
                st.metric("수익 요소", f"{stats['profit_factor']:.2f}", f"{stats['profit_factor'] - total_stats['profit_factor']:.2f}")
            
            # 현재 진행 중인 거래
            st.subheader("현재 진행 중인 거래")
            
            if not open_trades_df.empty:
                for _, trade in open_trades_df.iterrows():
                    col1, col2, col3, col4 = st.columns(4)
                    
                    direction_display = "롱(Long)" if trade['direction'] == 'long' else "숏(Short)"
                    
                    with col1:
                        st.markdown(f"**거래 ID:** {trade['id']}")
                        st.markdown(f"**방향:** {direction_display}")
                    
                    with col2:
                        st.markdown(f"**시작 시간:** {trade['timestamp'].strftime('%Y-%m-%d %H:%M')}")
                        st.markdown(f"**진입 가격:** ${trade['entry_price']:,.2f}")
                    
                    with col3:
                        st.markdown(f"**레버리지:** {trade['leverage']}x")
                        st.markdown(f"**투자 금액:** ${trade['investment_amount']:,.2f}")
                    
                    with col4:
                        st.markdown(f"**스탑로스:** ${trade['stop_loss_price']:,.2f}")
                        st.markdown(f"**테이크프로핏:** ${trade['take_profit_price']:,.2f}")
            else:
                st.info("현재 진행 중인 거래가 없습니다.")
            
            # 누적 수익 그래프
            st.subheader("누적 수익 그래프")
            
            if not filtered_df.empty:
                # 날짜별 정렬
                profit_df = filtered_df.sort_values('timestamp')
                profit_df['cumulative_profit'] = profit_df['profit_loss_percentage'].cumsum()
                
                fig = px.line(
                    profit_df, 
                    x='timestamp', 
                    y='cumulative_profit',
                    title='누적 수익률 추이',
                    labels={'timestamp': '날짜', 'cumulative_profit': '누적 수익률 (%)'}
                )
                
                fig.update_layout(
                    hovermode='x unified',
                    height=400,
                )
                
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("표시할 거래 데이터가 없습니다.")
            
            # 통계 차트
            col1, col2 = st.columns(2)
            
            with col1:
                st.subheader("포지션 방향별 성능")
                
                if not filtered_df.empty:
                    direction_performance = filtered_df.groupby('direction').agg({
                        'id': 'count',
                        'profit_loss': ['sum', lambda x: (x > 0).sum()],
                        'profit_loss_percentage': 'mean'
                    })
                    
                    direction_performance.columns = ['거래 수', '총 수익', '승리 수', '평균 수익률']
                    direction_performance['승률'] = direction_performance['승리 수'] / direction_performance['거래 수'] * 100
                    direction_performance = direction_performance.reset_index()
                    direction_performance['direction'] = direction_performance['direction'].map({'long': '롱(Long)', 'short': '숏(Short)'})
                    
                    fig = px.bar(
                        direction_performance,
                        x='direction',
                        y='평균 수익률',
                        color='승률',
                        text='거래 수',
                        labels={'direction': '포지션 방향', '평균 수익률': '평균 수익률 (%)', '승률': '승률 (%)'},
                        title='포지션 방향별 성능',
                        color_continuous_scale='RdYlGn'
                    )
                    
                    fig.update_layout(height=350)
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.info("표시할 거래 데이터가 없습니다.")
            
            with col2:
                st.subheader("레버리지별 성능")
                
                if not filtered_df.empty:
                    leverage_performance = filtered_df.groupby('leverage').agg({
                        'id': 'count',
                        'profit_loss': ['sum', lambda x: (x > 0).sum()],
                        'profit_loss_percentage': 'mean'
                    })
                    
                    leverage_performance.columns = ['거래 수', '총 수익', '승리 수', '평균 수익률']
                    leverage_performance['승률'] = leverage_performance['승리 수'] / leverage_performance['거래 수'] * 100
                    leverage_performance = leverage_performance.reset_index()
                    
                    fig = px.bar(
                        leverage_performance,
                        x='leverage',
                        y='평균 수익률',
                        color='승률',
                        text='거래 수',
                        labels={'leverage': '레버리지', '평균 수익률': '평균 수익률 (%)', '승률': '승률 (%)'},
                        title='레버리지별 성능',
                        color_continuous_scale='RdYlGn'
                    )
                    
                    fig.update_layout(height=350)
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.info("표시할 거래 데이터가 없습니다.")
            
            # 시간대별 성능 및 감성 점수별 성능
            col1, col2 = st.columns(2)
            
            with col1:
                st.subheader("시간대별 성능")
                
                if not filtered_df.empty:
                    filtered_df['hour'] = filtered_df['timestamp'].dt.hour
                    hourly_performance = filtered_df.groupby('hour').agg({
                        'id': 'count',
                        'profit_loss': lambda x: (x > 0).sum(),
                        'profit_loss_percentage': 'mean'
                    })
                    
                    hourly_performance.columns = ['거래 수', '승리 수', '평균 수익률']
                    hourly_performance['승률'] = hourly_performance['승리 수'] / hourly_performance['거래 수'] * 100
                    hourly_performance = hourly_performance.reset_index()
                    
                    fig = make_subplots(specs=[[{"secondary_y": True}]])
                    
                    fig.add_trace(
                        go.Bar(
                            x=hourly_performance['hour'],
                            y=hourly_performance['거래 수'],
                            name='거래 수',
                            opacity=0.7
                        ),
                        secondary_y=False
                    )
                    
                    fig.add_trace(
                        go.Scatter(
                            x=hourly_performance['hour'],
                            y=hourly_performance['승률'],
                            name='승률',
                            mode='lines+markers',
                            line=dict(color='red')
                        ),
                        secondary_y=True
                    )
                    
                    fig.update_layout(
                        title='시간대별 성능',
                        height=350
                    )
                    
                    fig.update_xaxes(title_text='시간 (Hour)')
                    fig.update_yaxes(title_text='거래 수', secondary_y=False)
                    fig.update_yaxes(title_text='승률 (%)', secondary_y=True)
                    
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.info("표시할 거래 데이터가 없습니다.")
            
            with col2:
                st.subheader("감성 점수별 성능")
                
                if not filtered_df.empty:
                    # 감성 점수 범주화
                    def categorize_sentiment(score):
                        if score <= -5:
                            return '매우 부정적 (-10~-6)'
                        elif score < 0:
                            return '부정적 (-5~-1)'
                        elif score == 0:
                            return '중립 (0)'
                        elif score <= 5:
                            return '긍정적 (1~5)'
                        else:
                            return '매우 긍정적 (6~10)'
                    
                    filtered_df['sentiment_category'] = filtered_df['sentiment_score'].apply(categorize_sentiment)
                    
                    sentiment_order = [
                        '매우 부정적 (-10~-6)',
                        '부정적 (-5~-1)',
                        '중립 (0)',
                        '긍정적 (1~5)',
                        '매우 긍정적 (6~10)'
                    ]
                    
                    sentiment_performance = filtered_df.groupby('sentiment_category').agg({
                        'id': 'count',
                        'profit_loss': lambda x: (x > 0).sum(),
                        'profit_loss_percentage': 'mean'
                    })
                    
                    sentiment_performance.columns = ['거래 수', '승리 수', '평균 수익률']
                    sentiment_performance['승률'] = sentiment_performance['승리 수'] / sentiment_performance['거래 수'] * 100
                    sentiment_performance = sentiment_performance.reset_index()
                    
                    # 감성 카테고리 정렬
                    sentiment_performance['sentiment_category'] = pd.Categorical(
                        sentiment_performance['sentiment_category'], 
                        categories=sentiment_order, 
                        ordered=True
                    )
                    sentiment_performance = sentiment_performance.sort_values('sentiment_category')
                    
                    fig = make_subplots(specs=[[{"secondary_y": True}]])
                    
                    fig.add_trace(
                        go.Bar(
                            x=sentiment_performance['sentiment_category'],
                            y=sentiment_performance['거래 수'],
                            name='거래 수',
                            opacity=0.7
                        ),
                        secondary_y=False
                    )
                    
                    fig.add_trace(
                        go.Scatter(
                            x=sentiment_performance['sentiment_category'],
                            y=sentiment_performance['승률'],
                            name='승률',
                            mode='lines+markers',
                            line=dict(color='green')
                        ),
                        secondary_y=True
                    )
                    
                    fig.update_layout(
                        title='감성 점수별 성능',
                        height=350
                    )
                    
                    fig.update_xaxes(title_text='감성 카테고리')
                    fig.update_yaxes(title_text='거래 수', secondary_y=False)
                    fig.update_yaxes(title_text='승률 (%)', secondary_y=True)
                    
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.info("표시할 거래 데이터가 없습니다.")
        
        # 탭 2: 거래 내역
        with tab2:
            st.title("거래 내역")
            
            # 상세 거래 내역 테이블
            if not filtered_df.empty:
                # 표시할 데이터 선택
                display_df = filtered_df[[
                    'id', 'timestamp', 'direction', 'entry_price', 'close_price', 
                    'leverage', 'position_size', 'investment_amount', 
                    'profit_loss', 'profit_loss_percentage', 'result_type', 'hold_duration'
                ]].copy()
                
                # 데이터 가공
                display_df['direction'] = display_df['direction'].map({'long': '롱(Long)', 'short': '숏(Short)'})
                display_df['result_type'] = display_df['result_type'].map({
                    'take_profit': '익절(TP)', 
                    'stop_loss': '손절(SL)'
                })
                display_df['hold_duration'] = display_df['hold_duration'].round(2).astype(str) + ' 분'
                
                # 컬럼명 변경
                display_df.columns = [
                    'ID', '진입 시간', '방향', '진입 가격', '종료 가격', 
                    '레버리지', '포지션 비율', '투자 금액', 
                    '수익/손실($)', '수익률(%)', '청산 유형', '보유 시간'
                ]
                
                # 배경색 스타일링 함수
                def highlight_profit(s):
                    if s.name == '수익률(%)':
                        return ['background-color: #d4f7d4' if v > 0 else 'background-color: #f7d4d4' for v in s]
                    return [''] * len(s)
                
                # 테이블 표시
                st.dataframe(
                    display_df.style.apply(highlight_profit),
                    use_container_width=True,
                    hide_index=True
                )
            else:
                st.info("표시할 거래 데이터가 없습니다.")
            
            # 최근 거래 상세 정보
            st.subheader("최근 거래 상세 정보")
            
            if not filtered_df.empty:
                # 최근 5개 거래 선택
                recent_trades = filtered_df.sort_values('timestamp', ascending=False).head(5)
                
                for _, trade in recent_trades.iterrows():
                    with st.expander(f"거래 ID: {trade['id']} - {trade['timestamp'].strftime('%Y-%m-%d %H:%M')}"):
                        col1, col2, col3 = st.columns(3)
                        
                        direction_display = "롱(Long)" if trade['direction'] == 'long' else "숏(Short)"
                        result_display = "익절(Take Profit)" if trade['result_type'] == 'take_profit' else "손절(Stop Loss)"
                        result_color = "green" if trade['profit_loss'] > 0 else "red"
                        
                        with col1:
                            st.markdown("**기본 정보**")
                            st.markdown(f"방향: {direction_display}")
                            st.markdown(f"레버리지: {trade['leverage']}x")
                            st.markdown(f"포지션 비율: {trade['position_size']*100:.1f}%")
                            st.markdown(f"투자 금액: ${trade['investment_amount']:.2f}")
                        
                        with col2:
                            st.markdown("**가격 정보**")
                            st.markdown(f"진입 가격: ${trade['entry_price']:.2f}")
                            st.markdown(f"종료 가격: ${trade['close_price']:.2f}")
                            st.markdown(f"스탑로스: ${trade['stop_loss_price']:.2f} (-{trade['stop_loss_percentage']}%)")
                            st.markdown(f"테이크프로핏: ${trade['take_profit_price']:.2f} (+{trade['take_profit_percentage']}%)")
                        
                        with col3:
                            st.markdown("**결과 정보**")
                            st.markdown(f"청산 유형: {result_display}")
                            st.markdown(f"수익/손실: <span style='color:{result_color}'>${trade['profit_loss']:.2f} ({trade['profit_loss_percentage']:.2f}%)</span>", unsafe_allow_html=True)
                            
                            hold_hours = trade['hold_duration'] / 60
                            if hold_hours < 1:
                                hold_time_display = f"{trade['hold_duration']:.1f} 분"
                            else:
                                hold_time_display = f"{hold_hours:.2f} 시간"
                            
                            st.markdown(f"보유 시간: {hold_time_display}")
                            st.markdown(f"감성 점수: {trade['sentiment_score']}")
            else:
                st.info("표시할 거래 데이터가 없습니다.")
        
        # 탭 3: 뉴스 분석
        with tab3:
            st.title("뉴스 감성 분석")
            
            if not news_df.empty:
                # 뉴스 감성 추이 그래프
                st.subheader("뉴스 감성 점수 추이")
                
                # 날짜별로 정렬
                news_plot_df = news_df.sort_values('timestamp')
                
                fig = px.line(
                    news_plot_df,
                    x='timestamp',
                    y='sentiment_score',
                    labels={'timestamp': '날짜', 'sentiment_score': '감성 점수 (-10 ~ +10)'},
                    title='비트코인 뉴스 감성 점수 추이'
                )
                
                # 중립 영역 표시
                fig.add_shape(
                    type="rect",
                    x0=news_plot_df['timestamp'].min(),
                    x1=news_plot_df['timestamp'].max(),
                    y0=-1,
                    y1=1,
                    fillcolor="lightgray",
                    opacity=0.3,
                    layer="below",
                    line_width=0,
                )
                
                # 가이드라인 추가
                fig.add_shape(
                    type="line",
                    x0=news_plot_df['timestamp'].min(),
                    x1=news_plot_df['timestamp'].max(),
                    y0=0,
                    y1=0,
                    line=dict(color="black", width=1, dash="dash"),
                )
                
                fig.update_layout(
                    hovermode='x unified',
                    height=400,
                )
                
                st.plotly_chart(fig, use_container_width=True)
                
                # 뉴스 감성 분포
                st.subheader("뉴스 감성 분포")
                
                # 감성 점수 범주화
                def categorize_sentiment(score):
                    if score <= -5:
                        return '매우 부정적 (-10~-6)'
                    elif score < 0:
                        return '부정적 (-5~-1)'
                    elif score == 0:
                        return '중립 (0)'
                    elif score <= 5:
                        return '긍정적 (1~5)'
                    else:
                        return '매우 긍정적 (6~10)'
                
                news_df['sentiment_category'] = news_df['sentiment_score'].apply(categorize_sentiment)
                
                sentiment_counts = news_df['sentiment_category'].value_counts().reset_index()
                sentiment_counts.columns = ['감성 카테고리', '개수']
                
                # 정렬 순서 정의
                sentiment_order = [
                    '매우 부정적 (-10~-6)',
                    '부정적 (-5~-1)',
                    '중립 (0)',
                    '긍정적 (1~5)',
                    '매우 긍정적 (6~10)'
                ]
                
                # 정렬을 위한 카테고리 변환
                sentiment_counts['감성 카테고리'] = pd.Categorical(
                    sentiment_counts['감성 카테고리'],
                    categories=sentiment_order,
                    ordered=True
                )
                sentiment_counts = sentiment_counts.sort_values('감성 카테고리')
                
                # 색상 매핑
                color_map = {
                    '매우 부정적 (-10~-6)': 'darkred',
                    '부정적 (-5~-1)': 'red',
                    '중립 (0)': 'gray',
                    '긍정적 (1~5)': 'green',
                    '매우 긍정적 (6~10)': 'darkgreen'
                }
                
                fig = px.bar(
                    sentiment_counts,
                    x='감성 카테고리',
                    y='개수',
                    color='감성 카테고리',
                    color_discrete_map=color_map,
                    title='뉴스 감성 분포'
                )
                
                fig.update_layout(height=400)
                st.plotly_chart(fig, use_container_width=True)
                
                # 최근 뉴스 내용
                st.subheader("최근 뉴스 내용")
                
                for i, news in enumerate(news_df.sort_values('timestamp', ascending=False).head(10).iterrows()):
                    news = news[1]
                    
                    # 감성 점수에 따른 색상 설정
                    if news['sentiment_score'] > 5:
                        score_color = 'darkgreen'
                    elif news['sentiment_score'] > 0:
                        score_color = 'green'
                    elif news['sentiment_score'] == 0:
                        score_color = 'gray'
                    elif news['sentiment_score'] > -6:
                        score_color = 'red'
                    else:
                        score_color = 'darkred'
                    
                    with st.expander(f"{news['timestamp'].strftime('%Y-%m-%d %H:%M')} - 감성 점수: {news['sentiment_score']}"):
                        st.markdown(f"<div style='border-left: 4px solid {score_color}; padding-left: 10px;'>{news['news_content']}</div>", unsafe_allow_html=True)
            else:
                st.info("뉴스 데이터가 없습니다.")
        
        # 탭 4: 실적 분석
        with tab4:
            st.title("트레이딩 실적 분석")
            
            if not filtered_df.empty:
                # 자본 곡선
                st.subheader("자본 곡선")
                
                # 날짜별 정렬
                equity_df = filtered_df.sort_values('timestamp').copy()
                
                # 투자 계산을 위한 초기 자본 입력
                initial_capital = st.slider(
                    "초기 자본 (USDT)",
                    min_value=100,
                    max_value=10000,
                    value=1000,
                    step=100
                )
                
                # 누적 수익과 자본 계산
                equity_df['profit_multiplier'] = 1 + (equity_df['profit_loss_percentage'] / 100)
                
                # 레버리지가 적용된 수익은 이미 profit_loss_percentage에 반영되어 있음
                equity_df['equity'] = initial_capital
                for i in range(len(equity_df)):
                    if i == 0:
                        equity_df.iloc[i, equity_df.columns.get_loc('equity')] = initial_capital * equity_df.iloc[i]['profit_multiplier']
                    else:
                        prev_equity = equity_df.iloc[i-1]['equity']
                        equity_df.iloc[i, equity_df.columns.get_loc('equity')] = prev_equity * equity_df.iloc[i]['profit_multiplier']
                
                # 자본 곡선 그래프
                fig = px.line(
                    equity_df,
                    x='timestamp',
                    y='equity',
                    title=f'자본 곡선 (초기 자본: {initial_capital} USDT)',
                    labels={'timestamp': '날짜', 'equity': '자본 (USDT)'}
                )
                
                # 초기 자본 참조선 추가
                fig.add_shape(
                    type="line",
                    x0=equity_df['timestamp'].min(),
                    x1=equity_df['timestamp'].max(),
                    y0=initial_capital,
                    y1=initial_capital,
                    line=dict(color="gray", width=1, dash="dash"),
                )
                
                fig.update_layout(
                    hovermode='x unified',
                    height=400,
                )
                
                st.plotly_chart(fig, use_container_width=True)
                
                # 성능 측정 지표들
                st.subheader("성능 측정 지표")
                
                final_capital = equity_df['equity'].iloc[-1]
                total_return = ((final_capital / initial_capital) - 1) * 100
                
                # 거래 기간 계산
                trading_days = (filtered_df['timestamp'].max() - filtered_df['timestamp'].min()).days
                if trading_days == 0:
                    trading_days = 1
                
                # 연간 수익률 (연복리)
                annual_return = ((1 + total_return/100) ** (365/trading_days) - 1) * 100
                
                # 최대 낙폭
                equity_df['peak'] = equity_df['equity'].cummax()
                equity_df['drawdown'] = (equity_df['equity'] - equity_df['peak']) / equity_df['peak'] * 100
                max_drawdown = equity_df['drawdown'].min()
                
                # 칼마 비율 (연간 수익률 / 최대 낙폭의 절대값)
                calmar_ratio = annual_return / abs(max_drawdown) if max_drawdown != 0 else 0
                
                # 평균 거래 수익
                avg_trade_profit = filtered_df['profit_loss_percentage'].mean()
                
                # 성공 vs 실패 거래
                winning_trades = len(filtered_df[filtered_df['profit_loss'] > 0])
                losing_trades = len(filtered_df[filtered_df['profit_loss'] <= 0])
                
                # 표 형태로 지표 표시
                metrics_data = {
                    '지표': [
                        '초기 자본',
                        '최종 자본',
                        '총 수익률',
                        '연간 수익률 (추정)',
                        '최대 낙폭',
                        '칼마 비율',
                        '승률',
                        '수익 요소',
                        '평균 거래 수익',
                        '총 거래 수',
                        '성공한 거래',
                        '실패한 거래',
                        '샤프 비율',
                        '평균 보유 시간'
                    ],
                    '값': [
                        f"{initial_capital:,.2f} USDT",
                        f"{final_capital:,.2f} USDT",
                        f"{total_return:,.2f}%",
                        f"{annual_return:,.2f}%",
                        f"{max_drawdown:,.2f}%",
                        f"{calmar_ratio:,.2f}",
                        f"{stats['win_rate']:,.2f}%",
                        f"{stats['profit_factor']:,.2f}",
                        f"{avg_trade_profit:,.2f}%",
                        f"{stats['total_trades']}",
                        f"{winning_trades}",
                        f"{losing_trades}",
                        f"{stats['sharpe_ratio']:,.2f}",
                        f"{stats['average_hold_time']:,.2f} 분"
                    ]
                }
                
                metrics_df = pd.DataFrame(metrics_data)
                st.table(metrics_df)
                
                # 최대 낙폭 그래프
                st.subheader("최대 낙폭 추이")
                
                fig = px.area(
                    equity_df,
                    x='timestamp',
                    y='drawdown',
                    title='최대 낙폭 추이',
                    labels={'timestamp': '날짜', 'drawdown': '낙폭 (%)'},
                    color_discrete_sequence=['red']
                )
                
                fig.update_layout(
                    hovermode='x unified',
                    height=350,
                )
                
                st.plotly_chart(fig, use_container_width=True)
                
                # 승패 분포 그래프
                col1, col2 = st.columns(2)
                
                with col1:
                    # 히스토그램 - 거래 수익률 분포
                    st.subheader("거래 수익률 분포")
                    
                    fig = px.histogram(
                        filtered_df,
                        x='profit_loss_percentage',
                        nbins=20,
                        title='거래 수익률 분포',
                        labels={'profit_loss_percentage': '수익률 (%)'},
                        color_discrete_sequence=['lightblue']
                    )
                    
                    # 평균 수익률 라인 추가
                    fig.add_vline(
                        x=avg_trade_profit,
                        line_dash="dash",
                        line_color="blue",
                        annotation_text=f"평균: {avg_trade_profit:.2f}%",
                        annotation_position="top right"
                    )
                    
                    # 0% 참조선 추가
                    fig.add_vline(
                        x=0,
                        line_dash="dash",
                        line_color="red"
                    )
                    
                    fig.update_layout(height=350)
                    st.plotly_chart(fig, use_container_width=True)
                
                with col2:
                    # 파이 차트 - 승패 비율
                    st.subheader("승패 비율")
                    
                    win_loss_data = pd.DataFrame({
                        '결과': ['승리', '패배'],
                        '거래 수': [winning_trades, losing_trades]
                    })
                    
                    fig = px.pie(
                        win_loss_data,
                        values='거래 수',
                        names='결과',
                        title='승패 비율',
                        color='결과',
                        color_discrete_map={'승리': 'green', '패배': 'red'}
                    )
                    
                    fig.update_traces(textinfo='percent+value')
                    fig.update_layout(height=350)
                    
                    st.plotly_chart(fig, use_container_width=True)
                
                # 스톱로스 vs 테이크프로핏 분석
                st.subheader("스톱로스(SL) vs 테이크프로핏(TP) 분석")
                
                # SL/TP 비율별 성능
                sl_tp_ratio_df = filtered_df.copy()
                sl_tp_ratio_df['risk_reward_ratio'] = sl_tp_ratio_df['take_profit_percentage'] / sl_tp_ratio_df['stop_loss_percentage']
                
                # 구간별로 분류
                def categorize_rr_ratio(ratio):
                    if ratio < 1:
                        return '< 1:1'
                    elif ratio < 1.5:
                        return '1:1 - 1:1.5'
                    elif ratio < 2:
                        return '1:1.5 - 1:2'
                    elif ratio < 3:
                        return '1:2 - 1:3'
                    else:
                        return '> 1:3'
                
                sl_tp_ratio_df['rr_category'] = sl_tp_ratio_df['risk_reward_ratio'].apply(categorize_rr_ratio)
                
                # 수익/손실 계산
                rr_performance = sl_tp_ratio_df.groupby('rr_category').agg({
                    'id': 'count',
                    'profit_loss': lambda x: (x > 0).sum(),
                    'profit_loss_percentage': 'mean'
                })
                
                rr_performance.columns = ['거래 수', '승리 수', '평균 수익률']
                rr_performance['승률'] = rr_performance['승리 수'] / rr_performance['거래 수'] * 100
                rr_performance = rr_performance.reset_index()
                
                # 정렬 순서 정의
                rr_order = ['< 1:1', '1:1 - 1:1.5', '1:1.5 - 1:2', '1:2 - 1:3', '> 1:3']
                
                # 정렬을 위한 카테고리 변환
                rr_performance['rr_category'] = pd.Categorical(
                    rr_performance['rr_category'],
                    categories=rr_order,
                    ordered=True
                )
                rr_performance = rr_performance.sort_values('rr_category')
                
                fig = px.bar(
                    rr_performance,
                    x='rr_category',
                    y='평균 수익률',
                    color='승률',
                    text='거래 수',
                    labels={
                        'rr_category': '리스크:리워드 비율',
                        '평균 수익률': '평균 수익률 (%)',
                        '승률': '승률 (%)'
                    },
                    title='리스크:리워드 비율별 성능',
                    color_continuous_scale='RdYlGn'
                )
                
                fig.update_layout(height=400)
                st.plotly_chart(fig, use_container_width=True)
                
                # 청산 유형별 분석
                sl_tp_counts = filtered_df['result_type'].value_counts().reset_index()
                sl_tp_counts.columns = ['청산 유형', '거래 수']
                sl_tp_counts['청산 유형'] = sl_tp_counts['청산 유형'].map({
                    'take_profit': '익절(TP)',
                    'stop_loss': '손절(SL)'
                })
                
                col1, col2 = st.columns(2)
                
                with col1:
                    st.subheader("청산 유형 분포")
                    
                    fig = px.pie(
                        sl_tp_counts,
                        values='거래 수',
                        names='청산 유형',
                        title='청산 유형 분포',
                        color='청산 유형',
                        color_discrete_map={'익절(TP)': 'green', '손절(SL)': 'red'}
                    )
                    
                    fig.update_traces(textinfo='percent+value')
                    fig.update_layout(height=350)
                    
                    st.plotly_chart(fig, use_container_width=True)
                
                with col2:
                    st.subheader("청산 유형별 평균 수익률")
                    
                    exit_type_performance = filtered_df.groupby('result_type').agg({
                        'profit_loss_percentage': 'mean'
                    }).reset_index()
                    
                    exit_type_performance['result_type'] = exit_type_performance['result_type'].map({
                        'take_profit': '익절(TP)',
                        'stop_loss': '손절(SL)'
                    })
                    
                    exit_type_performance.columns = ['청산 유형', '평균 수익률']
                    
                    fig = px.bar(
                        exit_type_performance,
                        x='청산 유형',
                        y='평균 수익률',
                        title='청산 유형별 평균 수익률',
                        labels={'평균 수익률': '평균 수익률 (%)'},
                        color='청산 유형',
                        color_discrete_map={'익절(TP)': 'green', '손절(SL)': 'red'}
                    )
                    
                    fig.update_layout(height=350)
                    st.plotly_chart(fig, use_container_width=True)
            
            else:
                st.info("표시할 거래 데이터가 없습니다.")
        
        # 탭 5: 설정 최적화
        with tab5:
            st.title("트레이딩 전략 최적화")
            
            if not filtered_df.empty:
                st.subheader("레버리지 최적화")
                
                # 레버리지별 성능
                leverage_performance = filtered_df.groupby('leverage').agg({
                    'id': 'count',
                    'profit_loss': ['sum', lambda x: (x > 0).sum()],
                    'profit_loss_percentage': ['mean', 'std']
                })
                
                leverage_performance.columns = ['거래 수', '총 수익', '승리 수', '평균 수익률', '표준편차']
                leverage_performance['승률'] = leverage_performance['승리 수'] / leverage_performance['거래 수'] * 100
                leverage_performance['샤프 비율'] = leverage_performance['평균 수익률'] / leverage_performance['표준편차'] if not leverage_performance['표준편차'].isnull().all() else 0
                leverage_performance = leverage_performance.reset_index()
                
                col1, col2 = st.columns(2)
                
                with col1:
                    fig = px.line(
                        leverage_performance,
                        x='leverage',
                        y='평균 수익률',
                        title='레버리지별 평균 수익률',
                        labels={'leverage': '레버리지', '평균 수익률': '평균 수익률 (%)'},
                        markers=True
                    )
                    
                    fig.update_layout(height=350)
                    st.plotly_chart(fig, use_container_width=True)
                
                with col2:
                    fig = px.line(
                        leverage_performance,
                        x='leverage',
                        y='승률',
                        title='레버리지별 승률',
                        labels={'leverage': '레버리지', '승률': '승률 (%)'},
                        markers=True
                    )
                    
                    fig.update_layout(height=350)
                    st.plotly_chart(fig, use_container_width=True)
                
                # 최적 레버리지 추천
                optimal_leverage = leverage_performance.loc[leverage_performance['샤프 비율'].idxmax()]['leverage'] if not leverage_performance['샤프 비율'].isnull().all() else None
                
                if optimal_leverage is not None:
                    st.success(f"📊 최적 레버리지 추천: {optimal_leverage}x (최고 샤프 비율 기준)")
                
                # 리스크:리워드 최적화
                st.subheader("리스크:리워드 비율 최적화")
                
                # 리스크:리워드 비율 계산
                risk_reward_df = filtered_df.copy()
                risk_reward_df['risk_reward_ratio'] = risk_reward_df['take_profit_percentage'] / risk_reward_df['stop_loss_percentage']
                
                # 구간으로 분류
                risk_reward_df['rr_bin'] = pd.cut(
                    risk_reward_df['risk_reward_ratio'],
                    bins=[0, 1, 1.5, 2, 2.5, 3, 10],
                    labels=['0-1', '1-1.5', '1.5-2', '2-2.5', '2.5-3', '3+']
                )
                
                # 구간별 성능
                rr_bin_performance = risk_reward_df.groupby('rr_bin').agg({
                    'id': 'count',
                    'profit_loss': lambda x: (x > 0).sum(),
                    'profit_loss_percentage': 'mean'
                })
                
                rr_bin_performance.columns = ['거래 수', '승리 수', '평균 수익률']
                rr_bin_performance['승률'] = rr_bin_performance['승리 수'] / rr_bin_performance['거래 수'] * 100
                rr_bin_performance = rr_bin_performance.reset_index()
                
                fig = px.bar(
                    rr_bin_performance,
                    x='rr_bin',
                    y='평균 수익률',
                    color='승률',
                    text='거래 수',
                    labels={
                        'rr_bin': '리스크:리워드 비율',
                        '평균 수익률': '평균 수익률 (%)',
                        '승률': '승률 (%)'
                    },
                    title='리스크:리워드 비율 최적화',
                    color_continuous_scale='RdYlGn'
                )
                
                fig.update_layout(height=400)
                st.plotly_chart(fig, use_container_width=True)
                
                # 최적 리스크:리워드 비율
                optimal_rr_bin = rr_bin_performance.loc[rr_bin_performance['평균 수익률'].idxmax()]['rr_bin']
                
                st.success(f"📊 최적 리스크:리워드 비율 추천: {optimal_rr_bin} (최고 평균 수익률 기준)")
                
                # 감성 점수 최적화
                st.subheader("뉴스 감성 점수 최적화")
                
                # 감성 점수 범주화
                def categorize_sentiment(score):
                    if score <= -5:
                        return '매우 부정적 (-10~-6)'
                    elif score < 0:
                        return '부정적 (-5~-1)'
                    elif score == 0:
                        return '중립 (0)'
                    elif score <= 5:
                        return '긍정적 (1~5)'
                    else:
                        return '매우 긍정적 (6~10)'
                
                filtered_df['sentiment_category'] = filtered_df['sentiment_score'].apply(categorize_sentiment)
                
                sentiment_order = [
                    '매우 부정적 (-10~-6)',
                    '부정적 (-5~-1)',
                    '중립 (0)',
                    '긍정적 (1~5)',
                    '매우 긍정적 (6~10)'
                ]
                
                sentiment_performance = filtered_df.groupby('sentiment_category').agg({
                    'id': 'count',
                    'profit_loss': lambda x: (x > 0).sum(),
                    'profit_loss_percentage': 'mean'
                })
                
                sentiment_performance.columns = ['거래 수', '승리 수', '평균 수익률']
                sentiment_performance['승률'] = sentiment_performance['승리 수'] / sentiment_performance['거래 수'] * 100
                sentiment_performance = sentiment_performance.reset_index()
                
                # 감성 카테고리 정렬
                sentiment_performance['sentiment_category'] = pd.Categorical(
                    sentiment_performance['sentiment_category'], 
                    categories=sentiment_order, 
                    ordered=True
                )
                sentiment_performance = sentiment_performance.sort_values('sentiment_category')
                
                # 방향별 감성 점수 최적화
                long_sentiment = filtered_df[filtered_df['direction'] == 'long'].groupby('sentiment_category').agg({
                    'id': 'count',
                    'profit_loss': lambda x: (x > 0).sum(),
                    'profit_loss_percentage': 'mean'
                })
                
                long_sentiment.columns = ['거래 수', '승리 수', '평균 수익률']
                long_sentiment['승률'] = long_sentiment['승리 수'] / long_sentiment['거래 수'] * 100
                long_sentiment = long_sentiment.reset_index()
                
                long_sentiment['sentiment_category'] = pd.Categorical(
                    long_sentiment['sentiment_category'], 
                    categories=sentiment_order, 
                    ordered=True
                )
                long_sentiment = long_sentiment.sort_values('sentiment_category')
                
                short_sentiment = filtered_df[filtered_df['direction'] == 'short'].groupby('sentiment_category').agg({
                    'id': 'count',
                    'profit_loss': lambda x: (x > 0).sum(),
                    'profit_loss_percentage': 'mean'
                })
                
                short_sentiment.columns = ['거래 수', '승리 수', '평균 수익률']
                short_sentiment['승률'] = short_sentiment['승리 수'] / short_sentiment['거래 수'] * 100
                short_sentiment = short_sentiment.reset_index()
                
                short_sentiment['sentiment_category'] = pd.Categorical(
                    short_sentiment['sentiment_category'], 
                    categories=sentiment_order, 
                    ordered=True
                )
                short_sentiment = short_sentiment.sort_values('sentiment_category')
                
                # 롱/숏 선택 라디오 버튼
                direction_radio = st.radio(
                    "포지션 방향 선택",
                    ["전체", "롱(Long)", "숏(Short)"],
                    horizontal=True
                )
                
                if direction_radio == "전체":
                    display_sentiment = sentiment_performance
                    title = '감성 점수별 성능 (전체)'
                elif direction_radio == "롱(Long)":
                    display_sentiment = long_sentiment
                    title = '감성 점수별 성능 (롱 전용)'
                else:
                    display_sentiment = short_sentiment
                    title = '감성 점수별 성능 (숏 전용)'
                
                fig = make_subplots(specs=[[{"secondary_y": True}]])
                
                # 막대 그래프: 평균 수익률
                fig.add_trace(
                    go.Bar(
                        x=display_sentiment['sentiment_category'],
                        y=display_sentiment['평균 수익률'],
                        name='평균 수익률',
                        opacity=0.7
                    ),
                    secondary_y=False
                )
                
                # 라인 그래프: 승률
                fig.add_trace(
                    go.Scatter(
                        x=display_sentiment['sentiment_category'],
                        y=display_sentiment['승률'],
                        name='승률',
                        mode='lines+markers',
                        line=dict(color='red')
                    ),
                    secondary_y=True
                )
                
                fig.update_layout(
                    title=title,
                    height=400
                )
                
                fig.update_xaxes(title_text='감성 카테고리')
                fig.update_yaxes(title_text='평균 수익률 (%)', secondary_y=False)
                fig.update_yaxes(title_text='승률 (%)', secondary_y=True)
                
                st.plotly_chart(fig, use_container_width=True)
                
                # 최적 감성 점수 추천
                if not display_sentiment.empty:
                    optimal_sentiment = display_sentiment.loc[display_sentiment['평균 수익률'].idxmax()]['sentiment_category']
                    
                    if direction_radio == "전체":
                        st.success(f"📊 최적 감성 점수 추천: {optimal_sentiment} (전체 포지션)")
                    elif direction_radio == "롱(Long)":
                        st.success(f"📊 최적 감성 점수 추천: {optimal_sentiment} (롱 포지션)")
                    else:
                        st.success(f"📊 최적 감성 점수 추천: {optimal_sentiment} (숏 포지션)")
                
                # 복합 최적화 추천
                st.subheader("통합 전략 최적화 추천")
                
                # 롱/숏 별 최적 설정
                long_df = filtered_df[filtered_df['direction'] == 'long']
                short_df = filtered_df[filtered_df['direction'] == 'short']
                
                if not long_df.empty:
                    # 롱 포지션 최적화
                    long_optimal_leverage = long_df.groupby('leverage').agg({
                        'profit_loss_percentage': 'mean'
                    }).idxmax()['profit_loss_percentage']
                    
                    long_df['risk_reward_ratio'] = long_df['take_profit_percentage'] / long_df['stop_loss_percentage']
                    long_df['rr_bin'] = pd.cut(
                        long_df['risk_reward_ratio'],
                        bins=[0, 1, 1.5, 2, 2.5, 3, 10],
                        labels=['0-1', '1-1.5', '1.5-2', '2-2.5', '2.5-3', '3+']
                    )
                    
                    long_optimal_rr = long_df.groupby('rr_bin').agg({
                        'profit_loss_percentage': 'mean'
                    }).idxmax()['profit_loss_percentage']
                    
                    long_optimal_sentiment = long_df.groupby('sentiment_category').agg({
                        'profit_loss_percentage': 'mean'
                    }).idxmax()['profit_loss_percentage']
                    
                    st.write("### 롱(Long) 포지션 최적 설정")
                    st.info(f"""
                    - 최적 레버리지: {long_optimal_leverage}x
                    - 최적 리스크:리워드 비율: {long_optimal_rr}
                    - 최적 뉴스 감성: {long_optimal_sentiment}
                    """)
                
                if not short_df.empty:
                    # 숏 포지션 최적화
                    short_optimal_leverage = short_df.groupby('leverage').agg({
                        'profit_loss_percentage': 'mean'
                    }).idxmax()['profit_loss_percentage']
                    
                    short_df['risk_reward_ratio'] = short_df['take_profit_percentage'] / short_df['stop_loss_percentage']
                    short_df['rr_bin'] = pd.cut(
                        short_df['risk_reward_ratio'],
                        bins=[0, 1, 1.5, 2, 2.5, 3, 10],
                        labels=['0-1', '1-1.5', '1.5-2', '2-2.5', '2.5-3', '3+']
                    )
                    
                    short_optimal_rr = short_df.groupby('rr_bin').agg({
                        'profit_loss_percentage': 'mean'
                    }).idxmax()['profit_loss_percentage']
                    
                    short_optimal_sentiment = short_df.groupby('sentiment_category').agg({
                        'profit_loss_percentage': 'mean'
                    }).idxmax()['profit_loss_percentage']
                    
                    st.write("### 숏(Short) 포지션 최적 설정")
                    st.info(f"""
                    - 최적 레버리지: {short_optimal_leverage}x
                    - 최적 리스크:리워드 비율: {short_optimal_rr}
                    - 최적 뉴스 감성: {short_optimal_sentiment}
                    """)
                
                # 시간대별 최적화
                st.subheader("트레이딩 시간대 최적화")
                
                filtered_df['hour'] = filtered_df['timestamp'].dt.hour
                hourly_performance = filtered_df.groupby('hour').agg({
                    'id': 'count',
                    'profit_loss': lambda x: (x > 0).sum(),
                    'profit_loss_percentage': 'mean'
                })
                
                hourly_performance.columns = ['거래 수', '승리 수', '평균 수익률']
                hourly_performance['승률'] = hourly_performance['승리 수'] / hourly_performance['거래 수'] * 100
                hourly_performance = hourly_performance.reset_index()
                
                # 방향별 시간대 성능
                if st.checkbox("방향별 시간대 성능 보기"):
                    direction_radio_hour = st.radio(
                        "포지션 방향 선택 (시간대)",
                        ["전체", "롱(Long)", "숏(Short)"],
                        horizontal=True,
                        key="hour_direction"
                    )
                    
                    if direction_radio_hour == "전체":
                        hour_df = filtered_df
                        title = '시간대별 성능 (전체)'
                    elif direction_radio_hour == "롱(Long)":
                        hour_df = filtered_df[filtered_df['direction'] == 'long']
                        title = '시간대별 성능 (롱 전용)'
                    else:
                        hour_df = filtered_df[filtered_df['direction'] == 'short']
                        title = '시간대별 성능 (숏 전용)'
                    
                    if not hour_df.empty:
                        hour_df['hour'] = hour_df['timestamp'].dt.hour
                        hourly_perf = hour_df.groupby('hour').agg({
                            'id': 'count',
                            'profit_loss': lambda x: (x > 0).sum(),
                            'profit_loss_percentage': 'mean'
                        })
                        
                        hourly_perf.columns = ['거래 수', '승리 수', '평균 수익률']
                        hourly_perf['승률'] = hourly_perf['승리 수'] / hourly_perf['거래 수'] * 100
                        hourly_perf = hourly_perf.reset_index()
                        
                        fig = make_subplots(specs=[[{"secondary_y": True}]])
                        
                        fig.add_trace(
                            go.Bar(
                                x=hourly_perf['hour'],
                                y=hourly_perf['평균 수익률'],
                                name='평균 수익률',
                                opacity=0.7
                            ),
                            secondary_y=False
                        )
                        
                        fig.add_trace(
                            go.Scatter(
                                x=hourly_perf['hour'],
                                y=hourly_perf['승률'],
                                name='승률',
                                mode='lines+markers',
                                line=dict(color='red')
                            ),
                            secondary_y=True
                        )
                        
                        fig.update_layout(
                            title=title,
                            height=400
                        )
                        
                        fig.update_xaxes(title_text='시간 (Hour)')
                        fig.update_yaxes(title_text='평균 수익률 (%)', secondary_y=False)
                        fig.update_yaxes(title_text='승률 (%)', secondary_y=True)
                        
                        st.plotly_chart(fig, use_container_width=True)
                        
                        # 최적 시간대 추천
                        best_hours = hourly_perf.nlargest(3, '평균 수익률')
                        
                        if direction_radio_hour == "전체":
                            st.success(f"📊 최적 거래 시간대 추천 (전체): {', '.join(best_hours['hour'].astype(str))}시")
                        elif direction_radio_hour == "롱(Long)":
                            st.success(f"📊 최적 거래 시간대 추천 (롱): {', '.join(best_hours['hour'].astype(str))}시")
                        else:
                            st.success(f"📊 최적 거래 시간대 추천 (숏): {', '.join(best_hours['hour'].astype(str))}시")
                
                # 최적의 트레이딩 파라미터 추천 요약
                st.subheader("트레이딩 봇 최적 파라미터 요약")
                
                col1, col2 = st.columns(2)
                
                with col1:
                    st.markdown("### 전체 최적 설정")
                    
                    # 전체 데이터에서 최적 레버리지
                    optimal_leverage_all = leverage_performance.loc[leverage_performance['평균 수익률'].idxmax()]['leverage']
                    
                    # 전체 데이터에서 최적 R:R 비율
                    optimal_rr_all = rr_bin_performance.loc[rr_bin_performance['평균 수익률'].idxmax()]['rr_bin']
                    
                    # 전체 데이터에서 최적 감성 점수
                    optimal_sentiment_all = sentiment_performance.loc[sentiment_performance['평균 수익률'].idxmax()]['sentiment_category']
                    
                    # 전체 데이터에서 최적 시간
                    best_hours_all = hourly_performance.nlargest(3, '평균 수익률')['hour'].tolist()
                    
                    st.markdown(f"""
                    **최적 레버리지:** {optimal_leverage_all}x
                    
                    **최적 리스크:리워드 비율:** {optimal_rr_all}
                    
                    **최적 뉴스 감성:** {optimal_sentiment_all}
                    
                    **최적 거래 시간:** {', '.join(map(str, best_hours_all))}시
                    """)
                
                with col2:
                    # 방향별 승률로 롱/숏 선택
                    if 'long' in filtered_df['direction'].values and 'short' in filtered_df['direction'].values:
                        long_win_rate = filtered_df[filtered_df['direction'] == 'long']['profit_loss'].apply(lambda x: x > 0).mean() * 100
                        short_win_rate = filtered_df[filtered_df['direction'] == 'short']['profit_loss'].apply(lambda x: x > 0).mean() * 100
                        
                        better_direction = "롱(Long)" if long_win_rate >= short_win_rate else "숏(Short)"
                        
                        st.markdown("### 방향별 성능 비교")
                        st.markdown(f"""
                        **롱(Long) 승률:** {long_win_rate:.2f}%
                        
                        **숏(Short) 승률:** {short_win_rate:.2f}%
                        
                        **추천 포지션 방향:** {better_direction}
                        """)
                
                # 최종 트레이딩 전략 자동 생성
                st.subheader("자동 생성된 트레이딩 전략")
                
                strategy_text = """
                ```python
                # 트레이딩 파라미터 자동 최적화 설정
                
                # 전체 최적 설정
                OPTIMAL_LEVERAGE = {0}  # 최적 레버리지
                OPTIMAL_RISK_REWARD = "{1}"  # 최적 리스크:리워드 비율
                OPTIMAL_SENTIMENT = "{2}"  # 최적 뉴스 감성 카테고리
                OPTIMAL_HOURS = {3}  # 최적 거래 시간
                
                # 롱 포지션 최적 설정
                LONG_OPTIMAL_LEVERAGE = {4}  # 롱 최적 레버리지
                LONG_OPTIMAL_RISK_REWARD = "{5}"  # 롱 최적 리스크:리워드 비율
                LONG_OPTIMAL_SENTIMENT = "{6}"  # 롱 최적 뉴스 감성
                
                # 숏 포지션 최적 설정
                SHORT_OPTIMAL_LEVERAGE = {7}  # 숏 최적 레버리지
                SHORT_OPTIMAL_RISK_REWARD = "{8}"  # 숏 최적 리스크:리워드 비율
                SHORT_OPTIMAL_SENTIMENT = "{9}"  # 숏 최적 뉴스 감성
                
                def get_optimized_parameters(direction, current_hour, sentiment_score):
                    \"\"\"
                    방향, 시간, 감성 점수에 따라 최적화된 트레이딩 파라미터 반환
                    \"\"\"
                    # 감성 점수 카테고리 변환
                    sentiment_category = categorize_sentiment(sentiment_score)
                    
                    # 시간이 최적 시간대인지 확인
                    is_optimal_hour = current_hour in OPTIMAL_HOURS
                    
                    # 방향별 최적 파라미터 선택
                    if direction == "long":
                        leverage = LONG_OPTIMAL_LEVERAGE
                        
                        # 리스크:리워드 비율에서 실제 값으로 변환
                        if LONG_OPTIMAL_RISK_REWARD == "0-1":
                            risk_reward = 0.8
                        elif LONG_OPTIMAL_RISK_REWARD == "1-1.5":
                            risk_reward = 1.2
                        elif LONG_OPTIMAL_RISK_REWARD == "1.5-2":
                            risk_reward = 1.7
                        elif LONG_OPTIMAL_RISK_REWARD == "2-2.5":
                            risk_reward = 2.2
                        elif LONG_OPTIMAL_RISK_REWARD == "2.5-3":
                            risk_reward = 2.7
                        else:  # "3+"
                            risk_reward = 3.5
                        
                        # 감성 점수가 최적 카테고리와 일치하는지 확인
                        is_optimal_sentiment = (sentiment_category == LONG_OPTIMAL_SENTIMENT)
                        
                    else:  # "short"
                        leverage = SHORT_OPTIMAL_LEVERAGE
                        
                        # 리스크:리워드 비율에서 실제 값으로 변환
                        if SHORT_OPTIMAL_RISK_REWARD == "0-1":
                            risk_reward = 0.8
                        elif SHORT_OPTIMAL_RISK_REWARD == "1-1.5":
                            risk_reward = 1.2
                        elif SHORT_OPTIMAL_RISK_REWARD == "1.5-2":
                            risk_reward = 1.7
                        elif SHORT_OPTIMAL_RISK_REWARD == "2-2.5":
                            risk_reward = 2.2
                        elif SHORT_OPTIMAL_RISK_REWARD == "2.5-3":
                            risk_reward = 2.7
                        else:  # "3+"
                            risk_reward = 3.5
                        
                        # 감성 점수가 최적 카테고리와 일치하는지 확인
                        is_optimal_sentiment = (sentiment_category == SHORT_OPTIMAL_SENTIMENT)
                    
                    # 조건에 따른 포지션 크기 조정
                    position_size = 0.1  # 기본 10%
                    
                    # 최적 시간대이면 포지션 크기 증가
                    if is_optimal_hour:
                        position_size += 0.05
                    
                    # 최적 감성 카테고리이면 포지션 크기 증가
                    if is_optimal_sentiment:
                        position_size += 0.05
                    
                    # 안전 제한 적용
                    position_size = min(position_size, 0.2)  # 최대 20%
                    
                    # 고정된 스탑로스 비율 (예: 1%)
                    stop_loss_percentage = 1.0
                    
                    # 리스크:리워드 비율에 따른 테이크프로핏 계산
                    take_profit_percentage = stop_loss_percentage * risk_reward
                    
                    return {
                        "direction": direction,
                        "leverage": leverage,
                        "position_size": position_size,
                        "stop_loss_percentage": stop_loss_percentage,
                        "take_profit_percentage": take_profit_percentage,
                        "risk_reward_ratio": risk_reward
                    }
                
                def categorize_sentiment(score):
                    \"\"\"감성 점수를 카테고리로 변환\"\"\"
                    if score <= -5:
                        return '매우 부정적 (-10~-6)'
                    elif score < 0:
                        return '부정적 (-5~-1)'
                    elif score == 0:
                        return '중립 (0)'
                    elif score <= 5:
                        return '긍정적 (1~5)'
                    else:
                        return '매우 긍정적 (6~10)'
                ```
                """.format(
                    optimal_leverage_all,  # {0}
                    optimal_rr_all,  # {1}
                    optimal_sentiment_all,  # {2}
                    best_hours_all,  # {3}
                    long_optimal_leverage if not long_df.empty else "None",  # {4}
                    long_optimal_rr if not long_df.empty else "None",  # {5}
                    long_optimal_sentiment if not long_df.empty else "None",  # {6}
                    short_optimal_leverage if not short_df.empty else "None",  # {7}
                    short_optimal_rr if not short_df.empty else "None",  # {8}
                    short_optimal_sentiment if not short_df.empty else "None"  # {9}
                )
                
                st.code(strategy_text, language="python")
                
                st.success("✅ 위 코드를 트레이딩 봇에 적용하여 최적화된 파라미터로 거래하세요!")
            
            else:
                st.info("표시할 거래 데이터가 없습니다. 최적화 분석을 하려면 충분한 거래 내역이 필요합니다.")
    except Exception as e:
        st.error(f"오류가 발생했습니다: {e}")
        st.info("데이터베이스가 올바르게 설정되었는지 확인하세요.")

if __name__ == "__main__":
    main()
