package handlers

import (
	"encoding/json"
	"net/http"

	"github.com/go-chi/chi/v5"

	"github.com/example/shortlink/internal/store"
)

// Handlers serves the shortlink HTTP API.
type Handlers struct {
	store   *store.Store
	baseURL string
}

type createRequest struct {
	URL string `json:"url"`
}

// New builds the HTTP handlers.
func New(s *store.Store, baseURL string) *Handlers {
	return &Handlers{store: s, baseURL: baseURL}
}

// Health reports liveness.
func (h *Handlers) Health(w http.ResponseWriter, r *http.Request) {
	w.WriteHeader(http.StatusOK)
}

// CreateLink stores a new short link.
func (h *Handlers) CreateLink(w http.ResponseWriter, r *http.Request) {
	var req createRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.URL == "" {
		http.Error(w, "invalid body", http.StatusBadRequest)
		return
	}
	code, err := h.store.Save(r.Context(), req.URL)
	if err != nil {
		http.Error(w, "store error", http.StatusInternalServerError)
		return
	}
	_ = json.NewEncoder(w).Encode(map[string]string{"short": h.baseURL + "/" + code})
}

// Resolve redirects a short code to its target URL.
func (h *Handlers) Resolve(w http.ResponseWriter, r *http.Request) {
	target, err := h.store.Lookup(r.Context(), chi.URLParam(r, "code"))
	if err != nil {
		http.NotFound(w, r)
		return
	}
	http.Redirect(w, r, target, http.StatusFound)
}
