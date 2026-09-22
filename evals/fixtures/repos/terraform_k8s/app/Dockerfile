FROM golang:1.22-alpine AS build
WORKDIR /src
COPY go.mod ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -o /out/shortlink .

FROM gcr.io/distroless/static-debian12
COPY --from=build /out/shortlink /shortlink
EXPOSE 8080
ENTRYPOINT ["/shortlink"]
