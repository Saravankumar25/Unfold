package store

import (
	"context"
	"crypto/rand"
	"encoding/base64"

	"github.com/jackc/pgx/v5/pgxpool"
)

// Store persists links in PostgreSQL.
type Store struct {
	pool *pgxpool.Pool
}

// Connect opens a connection pool.
func Connect(ctx context.Context, url string) (*Store, error) {
	pool, err := pgxpool.New(ctx, url)
	if err != nil {
		return nil, err
	}
	return &Store{pool: pool}, nil
}

// Close releases the pool.
func (s *Store) Close() {
	s.pool.Close()
}

// Save inserts a link and returns its short code.
func (s *Store) Save(ctx context.Context, url string) (string, error) {
	code := newCode()
	_, err := s.pool.Exec(ctx, "INSERT INTO links (code, url) VALUES ($1, $2)", code, url)
	return code, err
}

// Lookup returns the target URL for a code.
func (s *Store) Lookup(ctx context.Context, code string) (string, error) {
	var url string
	err := s.pool.QueryRow(ctx, "SELECT url FROM links WHERE code = $1", code).Scan(&url)
	return url, err
}

func newCode() string {
	b := make([]byte, 6)
	_, _ = rand.Read(b)
	return base64.RawURLEncoding.EncodeToString(b)
}
