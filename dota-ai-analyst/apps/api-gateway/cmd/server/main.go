package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"

	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/dota-ai-analyst/api-gateway/internal/config"
	"github.com/dota-ai-analyst/api-gateway/internal/handlers"
	"github.com/dota-ai-analyst/api-gateway/internal/router"
)

func main() {
	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil)).With("service", "api-gateway")
	cfg := config.Load()

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	pool, err := pgxpool.New(ctx, cfg.PostgresDSN)
	if err != nil {
		logger.Error("postgres_connect_failed", "error", err)
		os.Exit(1)
	}
	defer pool.Close()

	h := &handlers.Handlers{DB: pool}
	srv := &http.Server{
		Addr:    cfg.ListenAddr,
		Handler: router.New(h, logger, cfg.RateLimitRPS, cfg.RateLimitBurst),
	}

	go func() {
		logger.Info("listening", "addr", cfg.ListenAddr)
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			logger.Error("server_failed", "error", err)
			stop()
		}
	}()

	<-ctx.Done()
	logger.Info("shutting_down")
	shutdownCtx, cancel := context.WithTimeout(context.Background(), cfg.ShutdownTimeout)
	defer cancel()
	if err := srv.Shutdown(shutdownCtx); err != nil {
		logger.Error("shutdown_error", "error", err)
	}
}
