package main

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/example/shortlink/internal/handlers"
)

func TestHealth(t *testing.T) {
	h := handlers.New(nil, "http://localhost")
	rec := httptest.NewRecorder()
	h.Health(rec, httptest.NewRequest(http.MethodGet, "/healthz", nil))
	if rec.Code != http.StatusOK {
		t.Fatalf("status %d", rec.Code)
	}
}
