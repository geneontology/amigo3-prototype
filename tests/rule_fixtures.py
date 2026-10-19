"""Small source examples covering distinct mapping and rule semantics."""

import yaml


def write_rules(root):
    mapping = dict(
        subject_id="InterPro:IPR1",
        subject_label="Domain",
        predicate_id="skos:exactMatch",
        predicate_modifier="Not",
        object_id="GO:0004672",
        object_label="kinase activity",
        mapping_justification="semapv:ManualMappingCuration",
        comment="Source proposes removal <script>bad()</script>",
        custom_extension="keep this",
    )
    examples = {
        "projects/TEST/mappings.sssom.yaml": dict(
            mapping_set_id="https://example.org/set",
            description="Local interpretation",
            subject_source="InterPro",
            creator_id=["https://example.org/set-author"],
            mappings=[
                mapping,
                mapping | dict(object_id="sssom:NoTermFound", object_label="No term", predicate_modifier=""),
            ],
        ),
        "rules/arba/ARBA1/ARBA1-review.yaml": dict(
            id="ARBA1",
            rule_type="ARBA",
            status="COMPLETE",
            action="MODIFY",
            review_summary="narrow scope",
            rule=dict(
                condition_sets=[
                    dict(
                        number=1,
                        conditions=[
                            dict(condition_type="InterPro", curie="InterPro:IPR1"),
                            dict(condition_type="taxon", curie="NCBITaxon:9606", negated=True),
                        ],
                    ),
                    dict(number=2, conditions=[dict(condition_type="PANTHER", curie="PTHR00001:SF1")]),
                ],
                go_annotations=[dict(go_id="GO:0004672")],
                entries=[],
            ),
        ),
        "rules/arba/ARBA2/ARBA2-review.yaml": dict(
            id="ARBA2", rule_type="ARBA", action="DEPRECATE", rule=dict(condition_sets=[], go_annotations=[])
        ),
        "projects/PROTEOSTASIS/mappings/test.yaml": dict(
            id="pn:test",
            subject_curations=[
                dict(
                    subject_code="Chaperone",
                    subject_level="class",
                    curation_status="no_mapping",
                    rationale="Context only",
                    conditions=[dict(condition_level="branch", condition_code="Cytosol")],
                    excluded_subjects=[dict(condition_level="gene_symbol", condition_code="X")],
                )
            ],
        ),
        "interpro/pfam/PF1/PF1-review.yaml": dict(
            pfam_id="PF1",
            proposed_annotations=[
                dict(
                    term=dict(id="GO:0004672", label="kinase activity"),
                    relation=dict(id="RO:0002327", label="enables"),
                    status="PROPOSED",
                    counter_examples=[dict(accession="UniProt:P00002", reason="Not catalytic")],
                )
            ],
        ),
    }
    for name, value in examples.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(value))
