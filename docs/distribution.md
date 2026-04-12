# Distribution Strategy

Krakked is best positioned as proprietary, self-hosted Docker software for now.

## Recommended V1 Distribution Model

The cleanest first product shape is:

- customer runs Krakked through Docker or Docker Desktop
- customer keeps Kraken API credentials on their own machine or server
- you distribute versioned Docker images and Python artifacts
- you sell access, updates, and support without taking custody of funds or operating the trading service for the user

This fits the current architecture and keeps the trust boundary simple.

## License Recommendation

Do not add an open-source license file yet unless you intentionally want to open the product.

For a sellable v1, the safer default is:

- keep the repo private or source-available only on your terms
- publish a commercial EULA before broad distribution
- include a risk disclosure and no-performance-guarantee language

That legal/commercial package is a human task, not something the repo can safely invent on its own.

## Authentication Recommendation

For v1:

- keep exchange credentials local to the deployment
- require local UI/API authentication for any mutating controls
- avoid a mandatory cloud account for basic operation
- treat licensing and product activation as separate from exchange auth

This makes the product friendlier to privacy-conscious users and reduces the blast radius if a licensing system fails.

## Update Strategy Recommendation

Use explicit image tags and operator-controlled upgrades:

- pin `KRAKKED_IMAGE_TAG` to a release version
- instruct users to export before upgrades
- support rollback by switching back to a prior tag
- reserve `latest` for convenience, not production

## Suggested Commercial Path

If you want to sell Krakked without turning it into a hosted service immediately, the most realistic order is:

1. closed beta with manual customer onboarding
2. paid self-hosted licenses with update access
3. optional paid support and onboarding packages
4. only later, consider a hosted licensing portal or cloud sync features

## Human Help Needed

Before commercial launch, I strongly recommend real human review for:

- California/U.S. legal posture for automated trading software
- EULA, privacy policy, and refund terms
- export-control/tax/business structure questions
- branding, trademark, and business-name checks for `Krakked`
