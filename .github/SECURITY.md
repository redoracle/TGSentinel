# Security Policy

## 🔒 Supported Versions

We release patches for security vulnerabilities for the following versions:

| Version | Supported          |
| ------- | ------------------ |
| main    | :white_check_mark: |
| < 1.0   | :x:                |

## 🚨 Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

Instead, please report them via one of the following methods:

1. **GitHub Security Advisories** (Preferred)

   - Go to <https://github.com/redoracle/TGSentinel/security/advisories/new>
   - This ensures the vulnerability is handled privately

2. **Email**
   - Send details to the project maintainer
   - Include "TG Sentinel Security" in the subject line

### What to Include

Please include the following information:

- Type of vulnerability
- Full paths of source file(s) related to the vulnerability
- Location of the affected source code (tag/branch/commit or direct URL)
- Step-by-step instructions to reproduce the issue
- Proof-of-concept or exploit code (if possible)
- Impact of the vulnerability, including how an attacker might exploit it

## 🔐 Security Best Practices

### For Users

1. **Protect Your Session File**

   - Never share your `.session` file
   - Keep it secure with proper file permissions (600)
   - Rotate it if compromised

2. **API Credentials**

   - Never commit API credentials to Git
   - Use `.env` files (already in `.gitignore`)
   - Rotate credentials if exposed

3. **UI Security**

   - Set a strong `UI_SECRET_KEY` (32+ random bytes)
   - Enable `UI_LOCK_PASSWORD` for additional security
   - Use HTTPS in production deployments

4. **Docker Security**

   - Run containers as non-root user
   - Keep Docker images updated
   - Use Docker secrets for sensitive data

5. **Network Security**
   - Use VPN if additional IP masking is needed
   - Restrict Redis access to localhost only
   - Consider firewall rules for production

### For Developers

1. **Dependencies**

   - Keep dependencies up to date
   - Monitor Dependabot alerts
   - Review security advisories

2. **Code Review**

   - All PRs require review before merge
   - Security-sensitive changes need extra scrutiny
   - Follow principle of least privilege

3. **Testing**
   - Write tests for security-critical code
   - Test authentication/authorization flows
   - Validate input sanitization

## 📋 Security Checklist

- [ ] `.session` files never committed
- [ ] API credentials in `.env` only
- [ ] Strong `UI_SECRET_KEY` set
- [ ] UI lock password configured
- [ ] Docker images regularly updated
- [ ] Dependencies reviewed and updated
- [ ] Network properly secured
- [ ] Logs don't expose sensitive data

## 🔄 Update Process

1. We will acknowledge receipt of your vulnerability report within 48 hours
2. We will provide a detailed response within 7 days
3. We will work on a fix and keep you informed of progress
4. Once fixed, we will release a security advisory
5. Credit will be given to the reporter (unless anonymity is requested)

## 📝 Disclosure Policy

- Security advisories will be published after fixes are released
- Critical vulnerabilities: disclosed after 90 days or when fixed (whichever comes first)
- Non-critical vulnerabilities: disclosed after fix is released

## 🏆 Recognition

We appreciate security researchers who help keep TG Sentinel safe. Contributors who report valid security issues will be:

- Credited in the security advisory (if desired)
- Listed in our security acknowledgments
- Thanked publicly (with permission)

Thank you for helping keep TG Sentinel and its users safe! 🛡️
