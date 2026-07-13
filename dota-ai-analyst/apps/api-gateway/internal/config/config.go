package config

import (
	"os"
	"strconv"
	"time"
)

// Config задаёт параметры процесса API Gateway. Все значения берутся из
// переменных окружения, что соответствует запуску в Kubernetes (Гл. 12.8).
type Config struct {
	ListenAddr      string
	PostgresDSN     string
	ShutdownTimeout time.Duration
	RateLimitRPS    int
	RateLimitBurst  int
}

func Load() Config {
	return Config{
		ListenAddr:      getEnv("GATEWAY_LISTEN_ADDR", ":8080"),
		PostgresDSN:     getEnv("POSTGRES_DSN", "postgres://dota:dota_dev_password@localhost:5432/dota_analyst"),
		ShutdownTimeout: getDuration("GATEWAY_SHUTDOWN_TIMEOUT", 10*time.Second),
		RateLimitRPS:    getInt("GATEWAY_RATE_LIMIT_RPS", 20),
		RateLimitBurst:  getInt("GATEWAY_RATE_LIMIT_BURST", 40),
	}
}

func getEnv(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func getInt(key string, def int) int {
	if v := os.Getenv(key); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return def
}

func getDuration(key string, def time.Duration) time.Duration {
	if v := os.Getenv(key); v != "" {
		if d, err := time.ParseDuration(v); err == nil {
			return d
		}
	}
	return def
}
