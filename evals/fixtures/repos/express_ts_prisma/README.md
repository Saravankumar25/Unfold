# Blogpost

A small blogging platform: an Express + Prisma REST API (`src/`) and a React single-page app (`web/`).

## API

| Method | Path | Auth |
|---|---|---|
| POST | /api/auth/register | – |
| POST | /api/auth/login | – |
| GET | /api/users/me | JWT |
| GET | /api/posts | – |
| POST | /api/posts | JWT |
| POST | /api/posts/:id/comments | JWT |

Authentication uses short-lived JWTs signed with `JWT_SECRET`; passwords are hashed with bcrypt.

## Data model

Prisma models `User`, `Post` and `Comment` live in `prisma/schema.prisma` (PostgreSQL).

## Development

```bash
docker compose up --build
```
