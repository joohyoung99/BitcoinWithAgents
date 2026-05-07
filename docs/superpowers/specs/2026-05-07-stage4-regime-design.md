# Stage 4 Design — Regime Classifier

**날짜:** 2026-05-07  
**범위:** 4단계 — Regime 분류 + Gemini Advisory + 변경 감지 + state.json 업데이트  
**완료 기준:** `data/state.json`에 15분마다 `regime`, `regime_changed`, `regime_transition` 필드 갱신

---

## 선택한 접근법

**단일 파일 `src/regime.py`, chart.py 스타일 플랫 함수 구성.**

룰 기반 분류가 주(Primary), Gemini 2.5 Flash는 advisory 역할로만 사용. Gemini 의견이 달라도 룰 결과를 유지하고 로그만 남김. 알 수 없는 입력값은 무조건 Halt (fail-safe). chart와 regime은 단일 job 내 직렬 실행으로 순서 보장.

---

## 파일 구조

```
src/
├── models.py      # RegimeState dataclass 추가
├── regime.py      # 신규 — Regime Classifier
└── explorer.py    # main() 수정 — run_chart_and_regime 단일 job 등록

tests/
└── test_regime.py # 신규 — 9개 단위 테스트
```

---

## Regime 분류 로직

2축 조합 → 4가지 상태. 알 수 없는 값은 fail-safe로 "halt".

| trend_range | risk | regime |
|---|---|---|
| "trend" | "risk-on" | "normal" |
| "range" | "risk-on" | "caution" |
| "trend" | "risk-off" | "risk_off_trend" |
| "range" | "risk-off" | "halt" |
| 그 외 | 그 외 | "halt" |

```python
def classify_regime(trend_range: str, risk: str) -> str:
    table = {
        ("trend", "risk-on"):  "normal",
        ("range", "risk-on"):  "caution",
        ("trend", "risk-off"): "risk_off_trend",
        ("range", "risk-off"): "halt",
    }
    return table.get((trend_range, risk), "halt")  # fail-safe default
```

---

## Gemini Advisory

chart.py의 `score_signal` 방식과 동일한 패턴.

- 룰 기반 결과 + 현재 state 스냅샷을 Gemini Flash에 전달
- Gemini가 동의하면 그대로 사용
- Gemini가 다른 결과 반환 → 로그만 남기고 룰 결과 유지 (결정권은 항상 룰에)
- Gemini 호출 실패 → 룰 결과 그대로 사용 (예외 없이)

```python
def review_regime_with_gemini(
    regime: str,
    trend_range: str,
    risk: str,
    signal_summary: str,
) -> str:
    """Returns regime string. Falls back to rule-based regime on any failure."""
    try:
        # Gemini Flash 호출
        # 동의하면 regime 반환, 다르면 log + 원래 regime 반환
        ...
    except Exception:
        return regime
```

**Gemini 프롬프트 입력:**
- 현재 trend_range, risk
- 룰 기반 분류 결과
- chart signal_summary (요약)

**Gemini 출력 스키마 (strict):**
```json
{"regime": "<normal|caution|risk_off_trend|halt>", "comment": "<한 문장>"}
```

---

## 변경 감지

```python
prev_regime = state.get("BTCUSDT", {}).get("regime", "halt")
regime_changed = (new_regime != prev_regime)
regime_transition = f"{prev_regime.upper()}_TO_{new_regime.upper()}" if regime_changed else ""
```

전환 문자열 예시: `"NORMAL_TO_HALT"`, `"CAUTION_TO_NORMAL"`, `"HALT_TO_CAUTION"`

---

## state.json 추가 필드

기존 BTCUSDT 블록에 병합 (기존 필드 보존):

```json
{
  "meta": {"schema_version": 1, "updated_at": "..."},
  "BTCUSDT": {
    "regime": "normal",
    "prev_regime": "caution",
    "regime_changed": true,
    "regime_transition": "CAUTION_TO_NORMAL",
    "regime_summary": "EMA 정배열 확인됨, Gemini 동의",
    "regime_updated_at": "2026-05-07T12:06:00+00:00",
    "...": "기존 chart 필드들 유지"
  }
}
```

원자적 기록: `.tmp` → `os.replace()` (chart.py `update_state` 방식과 동일)

---

## 함수 구성

```
classify_regime(trend_range, risk) -> str
review_regime_with_gemini(regime, trend_range, risk, signal_summary) -> str
update_regime_state(regime, prev_regime, summary) -> None
run_regime_once() -> None
```

### run_regime_once() 흐름

```
1. state.json 읽기 → trend_range, risk, signal_summary, prev_regime 추출
2. state.json 없거나 stale(>30분) → regime = "halt", 바로 저장
3. classify_regime(trend_range, risk)
4. review_regime_with_gemini(regime, ...) → advisory 반영
5. 변경 감지: regime_changed, regime_transition
6. update_regime_state() → state.json 업데이트
7. print 로그
8. 전체 try/except — 절대 raise 안 함
```

---

## 스케줄링

`explorer.py main()` 수정:

```python
def run_chart_and_regime():
    run_chart_once()   # chart 완료 후
    run_regime_once()  # regime 실행 (직렬 보장)

scheduler.add_job(
    run_chart_and_regime,
    "interval",
    minutes=15,
    start_date=now + timedelta(minutes=5),
    id="chart_regime",
)
```

기존 `"chart"` job ID 제거, `"chart_regime"` 단일 job으로 교체.

---

## 테스트 계획 (`tests/test_regime.py`, 9개)

| 테스트 | 내용 |
|---|---|
| `test_classify_normal` | trend + risk-on → "normal" |
| `test_classify_caution` | range + risk-on → "caution" |
| `test_classify_risk_off_trend` | trend + risk-off → "risk_off_trend" |
| `test_classify_halt` | range + risk-off → "halt" |
| `test_classify_unknown_defaults_halt` | 알 수 없는 값 → "halt" |
| `test_review_regime_gemini_agrees` | Gemini 동의 → regime 유지 |
| `test_review_regime_gemini_fails` | Gemini 예외 → 룰 결과 반환 |
| `test_run_regime_once_writes_state` | state.json에 regime 필드 기록 확인 |
| `test_run_regime_once_detects_change` | prev≠new → regime_changed=True, transition 문자열 |

---

## 동적 거래 금액 (Stage 5 반영 사항)

Stage 5 주문 실행에서 아래와 같이 구현:

```python
def calc_trade_size(balance: float) -> float:
    """잔고의 10%, 최대 2000 USDT."""
    return min(2000.0, balance * 0.10)
```

- 잔고 20,000 USDT → 2,000 USDT (기존과 동일)
- 잔고 5,000 USDT → 500 USDT
- 잔고 1,000 USDT → 100 USDT (거래 가능 유지, 탈락 방지)

---

## 내결함성

- `run_regime_once()` 전체를 `try/except`로 래핑 — 어떤 예외도 프로세스 죽이지 않음
- Gemini 실패 → 룰 결과 사용
- state.json 없음/손상 → regime = "halt" 기본값
- state.json stale(>30분) → regime = "halt" (차트 데이터 신뢰 불가)
