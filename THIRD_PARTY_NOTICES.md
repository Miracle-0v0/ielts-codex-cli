# Third-Party Notices

IELTS Codex is distributed under the MIT License, but its optional online sync
can download and locally cache third-party lexical data. The third-party data
is not relicensed under the IELTS Codex MIT License.

## Open English WordNet 2025

The optional `/update` command uses the standard JSON release of:

- **Resource:** Open English WordNet 2025 Edition
- **Source:** <https://en-word.net/>
- **Release:** <https://github.com/globalwordnet/english-wordnet/releases/tag/2025-edition>
- **Copyright:** Copyright (c) 2019-present, The Open English WordNet Team
- **License:** Creative Commons Attribution 4.0 International (CC BY 4.0)
- **License text:** <https://creativecommons.org/licenses/by/4.0/legalcode>
- **Upstream license notice:** <https://github.com/globalwordnet/english-wordnet/blob/2025-edition/LICENSE.md>

Open English WordNet is derived from Princeton WordNet by the Open English
WordNet Community. Use of its content requires attribution to both Princeton
WordNet and the Open English WordNet team.

IELTS Codex downloads the upstream release only after the user explicitly runs
`/update`; ordinary startup remains offline.
It selects a matching sense for words already present in the bundled IELTS
vocabulary and stores the selected English definitions in a local JSON overlay.
This is an extraction and transformation of the upstream resource. IELTS Codex
does not import OEWN's full lexical database, and it does not use OEWN to
replace the project's curated Chinese meanings, translations, examples,
phonetics, topics, bands, or synonyms.

Suggested attribution:

> English definitions provided by Open English WordNet 2025, derived from
> Princeton WordNet, and used under CC BY 4.0. Definitions were selected and
> stored as a vocabulary-specific overlay by IELTS Codex.

No endorsement by the Open English WordNet team, the Global WordNet
Association, or Princeton University is implied.

## Princeton WordNet

Open English WordNet 2025 incorporates elements of the Princeton University
WordNet database. The upstream 2025 distribution requires preservation of the
underlying Princeton WordNet notice. Its source notice is available at:

<https://github.com/globalwordnet/english-wordnet/blob/2025-edition/WNDB_License.txt>

The applicable notice is reproduced below:

> Permission to use, copy, modify and distribute this software and database and
> its documentation for any purpose and without fee or royalty is hereby
> granted, provided that you agree to comply with the following copyright
> notice and statements, including the disclaimer, and that the same appear on
> ALL copies of the software, database and documentation, including
> modifications that you make for internal use or for distribution.
>
> WordNet 3.1 Copyright 2011 by Princeton University. All rights reserved.
>
> THIS SOFTWARE AND DATABASE IS PROVIDED "AS IS" AND PRINCETON UNIVERSITY MAKES
> NO REPRESENTATIONS OR WARRANTIES, EXPRESS OR IMPLIED. BY WAY OF EXAMPLE, BUT
> NOT LIMITATION, PRINCETON UNIVERSITY MAKES NO REPRESENTATIONS OR WARRANTIES OF
> MERCHANTABILITY OR FITNESS FOR ANY PARTICULAR PURPOSE OR THAT THE USE OF THE
> LICENSED SOFTWARE, DATABASE OR DOCUMENTATION WILL NOT INFRINGE ANY THIRD PARTY
> PATENTS, COPYRIGHTS, TRADEMARKS OR OTHER RIGHTS.
>
> The name of Princeton University or Princeton may not be used in advertising
> or publicity pertaining to distribution of the software and/or database.
> Title to copyright in this software, database and any associated
> documentation shall at all times remain with Princeton University and
> LICENSEE agrees to preserve same.

## Scope

The notices above apply to OEWN-derived content downloaded into
`oewn_overlay.json`. The repository's bundled base vocabulary remains under the
project's MIT License until and unless third-party definitions are deliberately
incorporated into it.
