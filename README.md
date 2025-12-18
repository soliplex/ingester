# Soliplex Ingester Documentation

Welcome to the Soliplex Ingester documentation! This directory contains comprehensive guides for using, configuring, and extending the system.

## Documentation Index

### Getting Started

- **[Getting Started Guide](GETTING_STARTED.md)** - Quick start tutorial for new users
  - Installation and setup
  - First batch processing
  - Basic operations
  - Common patterns

### Core Documentation

- **[Architecture Overview](ARCHITECTURE.md)** - System design and components
  - Component overview
  - Data flow diagrams
  - Technology stack
  - Scalability considerations

- **[API Reference](API.md)** - Complete REST API documentation
  - All endpoints with examples
  - Request/response formats
  - Data models
  - Error handling

- **[Workflow System](WORKFLOWS.md)** - Workflow concepts and configuration
  - Workflow step types
  - Configuration files
  - Execution model
  - Custom step handlers
  - Retry logic
  - Monitoring and troubleshooting

- **[Database Schema](DATABASE.md)** - Data models and relationships
  - All database tables
  - Field descriptions
  - Relationships and constraints
  - Query examples
  - Migration guide

- **[Configuration Guide](CONFIGURATION.md)** - Environment variables and settings
  - All configuration options
  - Environment-specific configs
  - Performance tuning
  - Secrets management
  - Troubleshooting

- **[CLI Reference](CLI.md)** - Command-line interface guide
  - All CLI commands
  - Usage examples
  - Deployment patterns
  - Systemd integration

## Quick Links

### For New Users
1. Start with [Getting Started](GETTING_STARTED.md)
2. Review [Architecture](ARCHITECTURE.md) to understand the system
3. Explore [API Reference](API.md) for integration

### For Developers
1. Read [Architecture](ARCHITECTURE.md) for system design
2. Study [Workflows](WORKFLOWS.md) to understand processing
3. Check [Database](DATABASE.md) for data models
4. Review [Configuration](CONFIGURATION.md) for environment setup

### For Operations
1. Review [Configuration](CONFIGURATION.md) for deployment settings
2. Use [CLI Reference](CLI.md) for management commands
3. Monitor using [API Reference](API.md) stats endpoints
4. Troubleshoot with [Workflows](WORKFLOWS.md) debugging section

## Document Summaries

### GETTING_STARTED.md
Step-by-step guide to:
- Install Soliplex Ingester
- Configure the system
- Create your first batch
- Ingest and process documents
- Monitor progress
- Deploy to production

**Audience:** New users, evaluators
**Time to complete:** 15-30 minutes

---

### ARCHITECTURE.md
Technical overview covering:
- System components (API server, workers, storage)
- Workflow execution model
- Data flow and processing pipeline
- Storage backends (database, files, vectors)
- Scalability and performance
- Extension points

**Audience:** Developers, architects
**Time to read:** 15-20 minutes

---

### API.md
Complete REST API reference including:
- Document ingestion endpoints
- Batch management APIs
- Workflow control and monitoring
- Parameter set configuration
- Data models and schemas
- Error handling

**Audience:** API consumers, integrators
**Format:** Reference documentation

---

### WORKFLOWS.md
In-depth workflow documentation:
- Workflow concepts and terminology
- Step types (ingest, parse, chunk, embed, store)
- YAML configuration format
- Lifecycle events
- Worker processing model
- Custom handler development
- Retry and error handling
- Performance tuning
- Troubleshooting guide

**Audience:** Power users, developers
**Time to read:** 25-30 minutes

---

### DATABASE.md
Database schema reference covering:
- All table definitions
- Field types and constraints
- Relationships and foreign keys
- Enums and constants
- Migration procedures
- Query examples
- Indexing recommendations
- Backup and maintenance

**Audience:** Database administrators, developers
**Format:** Reference documentation

---

### CONFIGURATION.md
Comprehensive configuration guide:
- All environment variables
- Default values and types
- Configuration validation
- Environment-specific configs
- Performance tuning parameters
- Worker settings
- Storage configuration
- Secrets management
- Docker and Kubernetes examples
- Troubleshooting

**Audience:** DevOps, system administrators
**Format:** Reference + guide

---

### CLI.md
Command-line tool reference:
- All CLI commands with options
- Usage examples
- Deployment patterns
- Systemd service files
- Docker usage
- Signal handling
- Platform notes
- Troubleshooting

**Audience:** System administrators, developers
**Format:** Reference documentation

---

## Additional Resources

### PlantUML Diagrams

- **models.plantuml** - Database entity relationship diagram
- **process.plantuml** - Workflow processing sequence diagram

View with PlantUML tools or online viewers.

### Configuration Examples

Check `config/` directory for:
- `workflows/*.yaml` - Example workflow definitions
- `params/*.yaml` - Example parameter sets

## Documentation Maintenance

### Contributing to Docs

When updating documentation:

1. **Keep it accurate** - Test all examples before committing
2. **Stay consistent** - Follow existing formatting and style
3. **Be comprehensive** - Cover edge cases and gotchas
4. **Add examples** - Show, don't just tell
5. **Update index** - Modify this README when adding docs

### Documentation Standards

- **Format:** Markdown with GitHub-flavored syntax
- **Code blocks:** Always specify language for syntax highlighting
- **Examples:** Include both curl and Python/script examples where applicable
- **Cross-references:** Link to related sections and documents
- **Versioning:** Note breaking changes and version requirements

### Feedback

Found an error or unclear section? Please:
- Open an issue describing the problem
- Suggest improvements via pull request
- Ask questions in discussions

## Version Information

- **Version:** 0.1.0
- **Python:** 3.12+
- **Last Updated:** 2025-01-16

## License

See LICENSE file in project root.

---

## Getting Help

### Documentation Issues
- Found a mistake? Open an issue
- Need clarification? Start a discussion
- Have suggestions? Submit a pull request

### Technical Support
- **Issues:** Bug reports and feature requests
- **Discussions:** Questions and community help
- **Email:** [Your support email]

### Related Documentation
- **HaikuRAG:** [Link to HaikuRAG docs]
- **Docling:** [Link to Docling docs]
- **LanceDB:** https://lancedb.com/docs/

---

## Quick Reference Card

```bash
# Installation
pip install -e .

# Configuration
si-cli validate-settings

# Database
si-cli db-init

# Server
si-cli serve --reload                    # Development
si-cli serve --host 0.0.0.0 --workers 4  # Production

# Workers
si-cli worker                            # Start worker

# Inspection
si-cli list-workflows                    # List workflows
si-cli dump-workflow batch               # View workflow
si-cli list-param-sets                   # List parameters
si-cli validate-haiku 1                  # Validate batch

# API
curl http://localhost:8000/docs          # Swagger UI
curl http://localhost:8000/api/v1/batch/ # List batches
```

---

Happy documenting! 📚
