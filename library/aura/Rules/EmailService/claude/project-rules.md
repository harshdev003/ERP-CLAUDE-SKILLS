---
description: Aura Service - Node.js Development Rules
globs: 
alwaysApply: true
---

# Aura Service - Node.js Development Rules

> **Shared by every backend service.** This file is identical in all 13 services, in `.claude/rules/` and `.cursor/rules/`. Change every copy in the same batch, and compare git blob hashes to check. Rules that apply to one service only go in that service's `.claude/rules/service-specific.md` (and `.cursor/rules/service-specific.mdc`).

This is the canonical baseline rule file for this repository.
Other files in `.claude/rules/` and `.cursor/rules/` should only add non-overlapping guidance; if any guidance conflicts, this file takes precedence.

## 🚨 CRITICAL: Always Use Correct Node.js Version and Lint

**MANDATORY**: Before making any code changes or running commands:

1. **Always run `nvm use` first** to ensure the correct Node.js version
2. **Run `npm run lint` after any code changes** to ensure code quality and style compliance
3. **Fix any linting errors before proceeding**
4. **Only proceed if linting passes without errors**

```bash
# Required workflow before any development work:
nvm use
npm run lint
```

## 🏗️ Architecture & Code Standards

### Project Structure
- **Express.js API**: RESTful API with proper middleware stack
- **Service Layer**: Business logic separated into service modules
- **Model Layer**: Data models with PostgreSQL integration
- **HTTP Handlers**: Request/response handling in dedicated handler files
- **Jobs**: Background job processing for scheduled tasks

### Code Quality Requirements
- **ESLint Compliance**: All code MUST pass ESLint without errors
- **Type Safety**: Use JSDoc for type annotations and parameter validation
- **Error Handling**: Implement proper error handling with try/catch and proper logging
- **Async/Await**: Prefer modern async/await over callbacks or promises
- **Memory Management**: Proper cleanup of resources and connections

### Model Method Patterns
- **CRUD Operations**: `create`, `retrieve`, `list`, `update`, `remove`, `delete`
- **Method Names**: Use simple action names that match handler names
- **Reserved Words**: For JavaScript reserved words, use descriptive names:
  - Method name: `deleteJournal` (avoids reserved `delete` keyword)
  - Export: `delete: deleteJournal`

### Router Pattern
- **Route Methods**: Use standard HTTP methods (GET, POST, PATCH, PUT, DELETE)
- **Route Paths**: Use RESTful conventions (`/:id` for specific resources)
- **Handler Names**: Match the simple action names from handlers

## 🎯 Critical Thinking & Problem Solving

### DO NOT:
- ❌ Agree with everything the user says without analysis
- ❌ Generate code that doesn't follow the established patterns
- ❌ Skip linting after code changes
- ❌ Ignore ESLint errors or warnings
- ❌ Suggest breaking changes to the architecture without justification

### DO:
- ✅ **Question assumptions** and validate requirements
- ✅ **Analyze trade-offs** before implementing solutions
- ✅ **Consider edge cases** and error scenarios
- ✅ **Follow established patterns** from the codebase
- ✅ **Lint and test** every code change
- ✅ **Document decisions** and reasoning

### Bug Fix Thinking Pattern
Reflect on 5-7 different possible sources of the problem, distill those down to 1-2 most likely different sources, then add logs to validate your assumptions before we move onto implementing actual code fix. Choose the best fix and don't leave it for the user.

## 🧪 Testing Requirements

### Unit Testing
- **Services**: Test all business logic and data transformations
- **Handlers**: Test API endpoint logic and error handling
- **Models**: Test data validation and database operations
- **Utils**: Test utility functions and helper methods

### Integration Testing
- **API Endpoints**: Test complete request/response cycles
- **Database Operations**: Test CRUD operations with test database
- **External Services**: Mock external API calls and test error scenarios

## 📱 Service-Specific Guidelines

### Database Operations
- **PostgreSQL**: Use connection pooling and proper query optimization
- **Migrations**: Use proper database migration scripts
- **Transactions**: Use transactions for multi-step operations
- **Indexing**: Ensure proper database indexing for performance

### Background Jobs
- **Job Processing**: Implement proper job queuing and processing
- **Error Handling**: Handle job failures gracefully
- **Retry Logic**: Implement exponential backoff for failed jobs
- **Monitoring**: Log job execution and performance metrics

## 🔧 Development Workflow

### Before Making Changes
1. **Switch to correct Node.js version** - `nvm use`
2. **Understand the requirement** - Ask clarifying questions if needed
3. **Analyze the current architecture** - Follow established patterns
4. **Consider the impact** - Will this change affect other services?

### During Implementation
1. **Follow service patterns** - Separate concerns properly
2. **Use proper error handling** - Don't ignore potential failures
3. **Implement logging** - Use the logger service for all operations
4. **Add validation** - Validate inputs and outputs

### After Implementation
1. **Run nvm use** - Ensure correct Node.js version
2. **Run linting** - `npm run lint`
3. **Test the functionality** - Verify it works as expected
4. **Check for regressions** - Ensure other features still work
5. **Document changes** - Update relevant documentation

## 🔄 Code Review Checklist

Before submitting any code changes, verify:

- [ ] Code passes ESLint without errors
- [ ] Follows established service patterns
- [ ] Includes proper error handling
- [ ] Uses appropriate logging
- [ ] Implements proper validation
- [ ] Respects security requirements
- [ ] Performance is acceptable
- [ ] Memory usage is optimized
- [ ] Error messages are user-friendly
- [ ] Uses logger instead of console statements
- [ ] Includes proper logging for important operations
- [ ] Uses appropriate log levels
- [ ] Includes metadata in log statements

## 🎯 Success Criteria

A successful implementation should:
1. **Use correct Node.js version** with `nvm use`
2. **Pass ESLint** on first attempt
3. **Follow established patterns** from the codebase
4. **Handle errors gracefully** with proper logging
5. **Maintain performance** standards
6. **Respect security** requirements
7. **Include proper testing** coverage
8. **Use proper logging** with appropriate levels and metadata
9. **Follow logging standards** with no console statements in production code

Remember: **Quality over speed**. It's better to take time to implement something correctly than to rush and create technical debt.

## 🔄 Git Workflow

### Branch Naming
- `feature/description` - New features
- `bugfix/description` - Bug fixes
- `refactor/description` - Code refactoring
- `quality/description` - Code quality improvements

### Commit Messages
- Use conventional commits format
- Include scope when relevant: `feat(auth): add JWT validation`
- Keep first line under 50 characters
- Add detailed description for complex changes

### Pull Request Process
- Open every PR as a draft (`gh pr create --draft`), run BugBot (`bugbot run`) and fix its findings until a run on the head commit is clean, then mark it ready (`gh pr ready`)
- Link to related issues
- Ensure all linting checks pass
- Request review from appropriate team members
- Ensure all CI checks pass before merging

## 🐛 Debugging Guidelines

### Common Debugging Tools
- Winston logger for application logs
- PostgreSQL query logs
- Express request/response logging
- Memory profiling with Node.js tools

### Documentation
Check the current implementation. Write beautiful docs with a natural explanation flow.
