package main

import (
	"context"
	"log"
	"net/http"
	"os"

	"github.com/go-chi/chi/v5"

	"github.com/example/shortlink/internal/handlers"
	"github.com/example/shortlink/internal/store"
)

func main() {
	ctx := context.Background()
	db, err := store.Connect(ctx, os.Getenv("DATABASE_URL"))
	if err != nil {
		log.Fatalf("connect: %v", err)
	}
	defer db.Close()

	h := handlers.New(db, os.Getenv("BASE_URL"))
	r := chi.NewRouter()
	r.Get("/healthz", h.Health)
	r.Post("/links", h.CreateLink)
	r.Get("/{code}", h.Resolve)

	addr := os.Getenv("LISTEN_ADDR")
	log.Printf("listening on %s", addr)
	log.Fatal(http.ListenAndServe(addr, r))
}
