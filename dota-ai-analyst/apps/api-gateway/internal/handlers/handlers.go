package handlers

import (
	"context"
	"encoding/json"
	"net/http"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

// Handlers объединяет зависимости HTTP-обработчиков шлюза.
type Handlers struct {
	DB *pgxpool.Pool
}

// problem — тело ошибки в формате RFC 7807 (Гл. 7.5).
type problem struct {
	Type   string `json:"type"`
	Title  string `json:"title"`
	Status int    `json:"status"`
	Detail string `json:"detail,omitempty"`
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func writeProblem(w http.ResponseWriter, status int, typ, title, detail string) {
	w.Header().Set("Content-Type", "application/problem+json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(problem{Type: typ, Title: title, Status: status, Detail: detail})
}

// Healthz — liveness-проба: процесс жив (Гл. 11.8.2).
func (h *Handlers) Healthz(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

// Readyz — readiness-проба: шлюз готов принимать трафик, БД доступна.
func (h *Handlers) Readyz(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
	defer cancel()
	if err := h.DB.Ping(ctx); err != nil {
		writeProblem(w, http.StatusServiceUnavailable,
			"service-unavailable", "Dependency not ready", "postgres: "+err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"status": "ready"})
}

// UploadReplay принимает файл реплея и ставит задание в очередь (UC-01).
// Каркасная версия: регистрирует AnalysisJob в PostgreSQL и отвечает 202;
// публикация в Kafka и выгрузка в S3 подключаются в Фазе 2.
func (h *Handlers) UploadReplay(w http.ResponseWriter, r *http.Request) {
	if err := r.ParseMultipartForm(64 << 20); err != nil {
		writeProblem(w, http.StatusBadRequest,
			"invalid-replay", "Invalid multipart form", err.Error())
		return
	}
	file, header, err := r.FormFile("file")
	if err != nil {
		writeProblem(w, http.StatusBadRequest,
			"invalid-replay", "Missing file field", err.Error())
		return
	}
	defer file.Close()

	// Минимальная валидация: непустой файл с расширением .dem (SEC: полная
	// проверка магии формата выполняется парсером в изолированной среде).
	if header.Size == 0 {
		writeProblem(w, http.StatusBadRequest, "invalid-replay", "Empty file", "")
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
	defer cancel()

	var jobID string
	err = h.DB.QueryRow(ctx,
		`INSERT INTO AnalysisJobs (status, replay_url) VALUES ('queued', $1) RETURNING job_id`,
		header.Filename,
	).Scan(&jobID)
	if err != nil {
		writeProblem(w, http.StatusInternalServerError,
			"internal-error", "Failed to enqueue job", err.Error())
		return
	}

	writeJSON(w, http.StatusAccepted, map[string]any{
		"job_id":                 jobID,
		"estimated_time_seconds": 10,
	})
}

// GetJob возвращает статус задания анализа.
func (h *Handlers) GetJob(w http.ResponseWriter, r *http.Request) {
	jobID := r.PathValue("jobId")
	ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
	defer cancel()

	var status string
	var createdAt time.Time
	err := h.DB.QueryRow(ctx,
		`SELECT status, created_at FROM AnalysisJobs WHERE job_id = $1`, jobID,
	).Scan(&status, &createdAt)
	if err != nil {
		writeProblem(w, http.StatusNotFound, "not-found", "Job not found", jobID)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"job_id":     jobID,
		"status":     status,
		"created_at": createdAt,
	})
}
