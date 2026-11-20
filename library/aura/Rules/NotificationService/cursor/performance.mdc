---
description: Performance guidelines for memory, API, and background jobs
globs: 
alwaysApply: true
---

# Performance Guidelines

> **Shared by every backend service.** This file is identical in all 13 services, in `.claude/rules/` and `.cursor/rules/`. Change every copy in the same batch, and compare git blob hashes to check. Rules that apply to one service only go in that service's `.claude/rules/service-specific.md` (and `.cursor/rules/service-specific.mdc`).

## Memory Management
- Use connection pooling for database connections
- Implement proper cleanup of resources
- Avoid memory leaks with proper event listener cleanup
- Use streaming for large data operations

## API Performance
- Implement proper caching strategies
- Use compression middleware for responses
- Optimize database queries
- Implement rate limiting

## Background Job Performance
- Use proper job queuing
- Implement job prioritization
- Monitor job execution times
- Implement job retry logic
