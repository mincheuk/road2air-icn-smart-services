# ROAD2AIR Team Project

## ✈️ 프로젝트 소개

**ROAD2AIR**는 인천공항 내 **지연/혼잡 등 예측 불가 상황**을 **실시간으로 인지·예측**하여 이용자가 공항 현황을 한눈에 파악하고 **이동 경로를 최적화**할 수 있도록 지원하는 **인천공항 통합 스마트 서비스(2주 MVP)** 입니다.

- 수행 기간: **2025.07.07 ~ 2025.07.18 (2주 MVP)**
- 데이터: 주차장 / 상업시설 / 출국장 혼잡도 / 항공편 / 환율

<img width="662" height="406" alt="image" src="https://github.com/user-attachments/assets/21efa4aa-10ac-4558-a713-04b66a814403" />

---

## 🏗️ 아키텍처 개요

📦 데이터 소스(공공데이터 포털 API) (주차/상업시설/출국장 혼잡, 항공편, 환율)  
↓  
🚌 수집 (Event Hubs)  
↓  
🧩 스트림 처리 (Azure Stream Analytics / SQL Window)    
↓  
💾 저장 (Cosmos DB: 도메인별 컨테이너 + 통합 컨테이너)  
↓  
⚙️ 서빙 및 자동화 (Azure Functions)    
↓  
📊 시각화 (Power BI Dashboard)  
↓  
🔔 운영 알림 (REST Refresh + Teams Webhook)

---

## 🛠️ 기술 스택

| 구분 | 사용 기술 |
|---|---|
| 스트리밍 수집 | Event Hubs |
| 스트림 처리 | Azure Stream Analytics (SQL) |
| 저장소 | Cosmos DB |
| 자동화/서빙 | Azure Functions |
| 시각화 | Power BI |
| 알림 | Teams Webhook |
| 버전관리 | Git + GitHub |

---

## ✅ 주요 역할 (정민철)

- **실시간 파이프라인 구축(2주 내 E2E 가동)**  
  Event Hubs → Stream Analytics(SQL Window/집계) → Cosmos DB → Power BI
  - 전처리(형 변환/필드 정규화), 스키마 매핑, 통합 컨테이너 적재 구조 설계
  ETL(배치/실시간) 파이프라인 설계·구축·운영 및 품질 검증(전/후 건수 확인)
  원천 데이터 수집·가공 및 품질 기준 수립/검수
  DB/저장소 설계·운영 및 데이터 흐름 관리
  Fabric/Azure Functions를 이용한 파이프라인 운영 자동화

- **Power BI 자동화**  
  - 유저용 대시보드 구성  
  - REST API 기반 새로고침 + Teams Webhook 알림 자동화
    
---

## 📈 업무 성과

- **실시간 조회 지연 완화** 및 현황 파악 속도 개선  
- **현장 의사결정 리드타임 단축** (상태 통합 가시화)  
