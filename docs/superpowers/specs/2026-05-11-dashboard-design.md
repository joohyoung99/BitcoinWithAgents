# Dashboard Design — BTC Agent Monitor

**Date:** 2026-05-11  
**Port:** 52090  
**Stack:** FastAPI + uvicorn (backend), HTML + Chart.js (frontend)

---

## Overview

외부에서 봇 상태를 모니터링할 수 있는 웹 대시보드.  
봇과 독립된 별도 프로세스로 실행 (`python dashboard.py`).  
`data/` 폴더의 JSON/CSV/log 파일을 직접 읽어서 서빙.

---

## Files

```
dashboard.py          ← FastAPI 서버 (신규)
dashboard/index.html  ← 프론트엔드 HTML (신규)
```

---

## Backend API (`dashboard.py`)

| Method | Path | Source | Description |
|--------|------|--------|-------------|
| GET | `/` | `dashboard/index.html` | HTML 페이지 |
| GET | `/api/state` | `data/state.json` | Regime, RSI, ADX, ATR, 현재가, entry signal |
| GET | `/api/explorer` | `data/explorer_report.json` | 매크로/ETF/심리/펀딩레이트/OI/L-S |
| GET | `/api/positions` | `data/positions.json` | 현재 오픈 포지션 목록 |
| GET | `/api/daily` | `data/daily.json` | 일일 통계 (trade_count, wins, losses, pnl) |
| GET | `/api/trades` | `data/trades.csv` | 거래 히스토리 (JSON 배열 변환) |
| GET | `/api/logs` | `data/system.log` | 최근 100줄 (줄바꿈 split) |

- 파일 없거나 파싱 실패 시 빈 값/빈 배열 반환 (500 에러 없음)
- CORS: `allow_origins=["*"]` (외부 브라우저 접근용)

---

## Frontend Layout (`dashboard/index.html`)

다크 테마. 단일 HTML 파일 (외부 CDN: Chart.js, Chart.js Doughnut).

```
┌─────────────────────────────────────────────────────────┐
│  헤더: 봇 이름 | Regime 배지 | BTC 현재가 | 업데이트 시각│
├──────────┬──────────┬──────────┬───────────────────────┤
│ 잔고(U)  │ 일일 PnL │ 승률     │ 오늘 거래수            │
├──────────┴──────────┴──────────┴───────────────────────┤
│  RSI 게이지  |  ADX 게이지  |  Fear&Greed 게이지       │
│  (Chart.js Doughnut 반원형)                             │
├─────────────────────────┬───────────────────────────────┤
│  누적 PnL 차트 (라인)   │  매크로 지표 카드             │
│  trades.csv 기반        │  Fed Rate / ETF 자금흐름      │
│                         │  Funding Rate / OI / L/S비율 │
├─────────────────────────┴───────────────────────────────┤
│  현재 포지션 테이블 (방향/레버리지/진입가/SL/TP/미실현손익) │
├─────────────────────────────────────────────────────────┤
│  시스템 로그 (스크롤, 최근 100줄, 모노스페이스)          │
└─────────────────────────────────────────────────────────┘
```

### Auto-refresh
- `setInterval(refresh, 10000)` — 10초마다 모든 `/api/*` 폴링
- fetch 실패 시 헤더에 빨간 "연결 끊김" 배너 표시

### Regime 배지 색상
| Regime | 색상 |
|--------|------|
| normal | 🟢 초록 |
| caution | 🟡 노랑 |
| risk_off_trend | 🟠 주황 |
| halt | 🔴 빨강 |

---

## Dependencies

```
fastapi
uvicorn[standard]
```

기존 프로젝트 의존성에 추가. 봇과 독립 실행이므로 봇에는 영향 없음.

---

## Run

```bash
python dashboard.py
# → http://0.0.0.0:52090
```

---

## Error Handling

- 각 `/api/*` 엔드포인트는 try/except 래핑 → 파일 없거나 파싱 오류 시 빈 기본값 반환
- 프론트엔드는 null/undefined 값 방어 처리 (대시 `-` 또는 `0` 표시)
- `trades.csv` 없을 시 누적 PnL 차트는 빈 상태로 렌더링

---

## Out of Scope

- 인증/로그인 (데모 환경, 내부망 모니터링 전제)
- 주문 실행 기능 (읽기 전용)
- WebSocket 실시간 스트림 (폴링으로 충분)
