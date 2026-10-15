# DORA 지표 자동 수집 & 대시보드

GitHub Actions로 DORA 4대 지표(배포 빈도, 변경 리드 타임, 평균 복구 시간, 변경 실패율)를 자동 수집하고,
Chart.js 대시보드와 이 README의 주간 리포트로 보여 주는 프로젝트입니다.

- 📈 **대시보드**: https://oocumu-school.github.io/oss-dora/ 
- ⚙️ **워크플로우**: [`dora-metrics.yml`](.github/workflows/dora-metrics.yml) (지표 수집) · [`deploy.yml`](.github/workflows/deploy.yml) (배포)
- 🧮 **수집 스크립트**: [`scripts/collect_dora.py`](scripts/collect_dora.py)

## 1. 과제 수행 항목

| 요구 사항 | 구현 내용 |
|---|---|
| GitHub Actions로 DORA 4대 지표 자동 수집 | `dora-metrics.yml`이 배포 후·매주 월요일·수동 실행 시 GitHub API로 지표를 계산 |
| 대시보드 시안/구현 | `dashboard/index.html` (Chart.js), GitHub Pages로 배포 |
| README에 이미지 첨부 | `docs/dora/dora-chart.png`가 자동 생성·커밋되어 아래 리포트에 표시 |
| (선택) JSON 아티팩트 저장 | 매 실행마다 `dora-metrics-<번호>` 아티팩트 업로드 (`latest.json`, `history.json`, 그래프) |
| (선택) 주간 보고서 자동 생성 | 아래 **3. 주간 리포트** 구간이 README에 자동으로 갱신됨 |

## 2. 동작 구조

```
main 에 push ─▶ Deploy 워크플로우 (GitHub Pages 배포)   ── 이 실행 기록이 "배포" 데이터
                      │ 완료되면
                      ▼
            DORA Metrics 워크플로우  ◀── 매주 월요일 00:05 KST / 수동 실행
                      │  GitHub API: 배포 실행 기록 · 커밋 비교 · incident 이슈
                      ▼
      metrics/latest.json, metrics/history.json  ──▶ JSON 아티팩트 업로드
      docs/dora/dora-chart.png                    ──▶ README 그래프
      README.md (3. 주간 리포트 구간)              ──▶ 자동 커밋
```

### 지표 정의

| 지표 | 계산 방법 | 데이터 출처 |
|---|---|---|
| 배포 빈도 (Deployment Frequency) | 성공한 `Deploy` 실행 수 / 주 | Actions 실행 기록 |
| 변경 리드 타임 (Lead Time for Changes) | 커밋 작성 시각 → 그 커밋이 포함된 첫 성공 배포 완료 시각, **중앙값** | 직전 성공 배포와 이번 배포 사이의 커밋 비교 |
| 평균 복구 시간 (MTTR) | 배포 실패 → 다음 성공 배포까지 시간 + `incident` 라벨 이슈의 생성 → 종료 시간, **평균** | Actions 실행 기록, Issues |
| 변경 실패율 (Change Failure Rate) | 실패한 배포 수 ÷ 전체 배포 시도 수 (취소·건너뜀 제외) | Actions 실행 기록 |

### 등급 기준 (Accelerate State of DevOps 보고서 구간을 단순화)

| 등급 | 배포 빈도 | 리드 타임 | MTTR | 변경 실패율 |
|---|---|---|---|---|
| 🟢 Elite | 주 7회 이상 | 1일 미만 | 1시간 미만 | 15% 이하 |
| 🔵 High | 주 1회 이상 | 1주 미만 | 1일 미만 | 30% 이하 |
| 🟡 Medium | 월 1회 이상 | 1개월 미만 | 1주 미만 | 45% 이하 |
| 🔴 Low | 그 미만 | 1개월 이상 | 1주 이상 | 45% 초과 |

> 주 단위는 한국 시간 월요일 00:00 ~ 일요일 23:59입니다. 최근 8주는 실행할 때마다 API에서 다시 계산합니다.

<!-- DORA:START -->

## 3. 주간 리포트 (자동 생성)

> 자동 생성: 2026-10-15 21:32 KST · 이번 주: 2026-10-12 ~ (진행 중) · 지난 주: 2026-10-05 ~ 2026-10-11 · [수집 실행 로그](https://github.com/oocumu-school/oss-dora/actions/runs/38735385539)

| 지표 | 이번 주 (진행 중) | 지난 주 | 최근 8주 | 등급 (최근 8주) |
|---|---:|---:|---:|:---:|
| 배포 빈도 | 2회 | 0회 | 주 0.26회 | 🟡 Medium |
| 변경 리드 타임 | 3분 | — | 3분 | 🟢 Elite |
| 평균 복구 시간 (MTTR) | — | — | — | — |
| 변경 실패율 | 0.0% | — | 0.0% | 🟢 Elite |

![DORA 지표 추이](docs/dora/dora-chart.png)

<details><summary>주별 기록 펼치기</summary>

| 주 (월요일) | 배포 | 실패 | 리드 타임 | MTTR | 실패율 |
|---|---:|---:|---:|---:|---:|
| 2026-10-12 (진행 중) | 2 | 0 | 3분 | — | 0.0% |
| 2026-10-05 | 0 | 0 | — | — | — |
| 2026-09-28 | 0 | 0 | — | — | — |
| 2026-09-21 | 0 | 0 | — | — | — |
| 2026-09-14 | 0 | 0 | — | — | — |
| 2026-09-07 | 0 | 0 | — | — | — |
| 2026-08-31 | 0 | 0 | — | — | — |
| 2026-08-24 | 0 | 0 | — | — | — |

</details>

원본 데이터: [`metrics/latest.json`](metrics/latest.json) · [`metrics/history.json`](metrics/history.json)

<!-- DORA:END -->

## 4. 대시보드

<!-- ✏️ 대시보드 화면을 캡처해 docs/screenshots/dashboard.png 로 저장 -->
![대시보드 화면](docs/screenshots/dashboard.png)

- KPI 카드 4개: 최근 8주 값과 등급
- 차트 4개: 주별 배포 횟수(성공/실패), 리드 타임, MTTR, 변경 실패율 (연한 막대·점선 = 진행 중인 이번 주)
- 하단 표: 주별 전체 기록

## 5. 실행 결과

<!-- ✏️ Actions 탭에서 DORA Metrics 실행 성공 화면 캡처 -->
![워크플로우 실행 결과](docs/screenshots/actions-run.png)

<!-- ✏️ 실행 상세 화면 아래쪽 Artifacts 영역 캡처 -->
![JSON 아티팩트](docs/screenshots/artifact.png)

## 6. 결과 분석

<!-- ✏️ 3. 주간 리포트의 숫자를 보고 직접 작성. 아래는 예시 문장이니 실제 값에 맞게 고치기 -->

- **배포 빈도**: 최근 8주 평균 주 N회(등급). 작은 단위로 자주 push 하는 방식이라 ...
- **변경 리드 타임**: 중앙값 N시간(등급). 커밋 후 바로 main에 push 하기 때문에 ...
- **MTTR**: `simulate_failure`로 배포 실패를 N회 발생시켰고, 다음 정상 배포까지 평균 N분이 걸렸다(등급).
- **변경 실패율**: 전체 N회 중 N회 실패로 N%(등급). 실패는 모두 의도적인 시뮬레이션이다.

## 7. 개선 방향 및 한계

- 배포를 "Deploy 워크플로우 성공"으로, 장애를 "배포 실패 + `incident` 이슈"로 근사했다. 실제 서비스라면 모니터링 알림이 incident 이슈를 자동으로 만들도록 연결해야 운영 장애까지 MTTR에 반영된다.
- 리드 타임은 커밋 작성 시각 기준이라 rebase/amend로 시각이 바뀐 커밋은 실제보다 짧게 잡힐 수 있다. PR 기반 개발이라면 PR 생성 시각을 쓰는 방식도 고려할 수 있다.
- 개인 과제 저장소라 배포 횟수가 적어 주별 값의 변동이 크다. 데이터가 쌓일수록 추세가 안정된다.
- 자동 커밋은 `GITHUB_TOKEN`으로 하므로 Deploy를 다시 실행하지 않아 배포 횟수에 섞이지 않는다.
