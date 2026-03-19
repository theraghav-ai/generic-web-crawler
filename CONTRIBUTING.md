# Contributing

Thanks for your interest in contributing! Here's how to get started.

## Setup

1. Fork and clone the repository
2. Run `./manage.sh setup` to install dependencies
3. Make your changes on a feature branch

## Code Style

- Follow existing code conventions in the project
- Keep functions focused and well-named
- Add logging where it aids debugging

## Submitting Changes

1. Create a branch: `git checkout -b feature/your-feature`
2. Make your changes and test them with a real crawl
3. Commit with a clear message describing what changed
4. Push and open a pull request

## Reporting Issues

Open an issue with:
- What you expected to happen
- What actually happened
- The URL you were crawling (if applicable)
- Relevant log output

## Guidelines

- Respect `robots.txt` by default — the `--ignore-robots` flag is for user discretion
- Be mindful of crawl rates; don't remove rate-limiting safeguards
- Test with at least one real website before submitting crawler changes
