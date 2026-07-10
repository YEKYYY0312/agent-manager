## Summary

-

## Verification

- [ ] `py -m pytest`
- [ ] `npm run test:data`
- [ ] `npm run lint`
- [ ] `npm run build`
- [ ] `npm audit --audit-level=high --registry=https://registry.npmjs.org/`

## Security checklist

- [ ] No raw secrets are logged, persisted to localStorage, committed, or printed.
- [ ] Local code execution remains explicitly gated.
- [ ] Remote network endpoints are validated or documented.
- [ ] New trace payload persistence has a size limit or clear trust boundary.

