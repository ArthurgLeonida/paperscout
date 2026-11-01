import pytest

from paperscout.identifiers import IdentifierError, normalize_title, parse_paper_ref

SHA = "649def34f8be52c8b66281af98ae884c09aef38b"


@pytest.mark.parametrize(
    "text, kind, value, s2_id",
    [
        ("10.1145/3592433", "doi", "10.1145/3592433", "DOI:10.1145/3592433"),
        ("doi:10.1145/3592433.", "doi", "10.1145/3592433", "DOI:10.1145/3592433"),
        ("https://doi.org/10.1145/3592433", "doi", "10.1145/3592433", "DOI:10.1145/3592433"),
        ("https://dx.doi.org/10.1000/ABC.Def", "doi", "10.1000/abc.def", "DOI:10.1000/abc.def"),
        ("2308.04079", "arxiv", "2308.04079", "ARXIV:2308.04079"),
        ("2308.04079v2", "arxiv", "2308.04079", "ARXIV:2308.04079"),
        ("arXiv:2308.04079v3", "arxiv", "2308.04079", "ARXIV:2308.04079"),
        ("https://arxiv.org/abs/2308.04079v1", "arxiv", "2308.04079", "ARXIV:2308.04079"),
        ("https://arxiv.org/pdf/2308.04079.pdf", "arxiv", "2308.04079", "ARXIV:2308.04079"),
        ("hep-th/9901001", "arxiv", "hep-th/9901001", "ARXIV:hep-th/9901001"),
        ("https://doi.org/10.48550/arXiv.2308.04079", "arxiv", "2308.04079", "ARXIV:2308.04079"),
        (SHA, "s2", SHA, SHA),
        (f"https://www.semanticscholar.org/paper/Some-Title/{SHA}", "s2", SHA, SHA),
        ("CorpusId:215416146", "corpus", "215416146", "CorpusId:215416146"),
        ("https://pubmed.ncbi.nlm.nih.gov/31452104/", "pmid", "31452104", "PMID:31452104"),
        ("https://aclanthology.org/2020.acl-main.447/", "acl", "2020.acl-main.447", "ACL:2020.acl-main.447"),
        ("https://dl.acm.org/doi/10.1145/3592433", "doi", "10.1145/3592433", "DOI:10.1145/3592433"),
    ],
)
def test_parse_identifiers(text, kind, value, s2_id):
    ref = parse_paper_ref(text)
    assert (ref.kind, ref.value, ref.s2_id) == (kind, value, s2_id)


def test_title_fallback_preserves_text():
    ref = parse_paper_ref("  3D Gaussian   Splatting for Real-Time Radiance Field Rendering ")
    assert ref.is_title
    assert ref.value == "3D Gaussian Splatting for Real-Time Radiance Field Rendering"
    with pytest.raises(ValueError):
        _ = ref.s2_id


def test_unsupported_url_is_explicit():
    with pytest.raises(IdentifierError, match="Unsupported paper URL host"):
        parse_paper_ref("https://example.com/some/paper")


def test_invalid_ids_are_rejected():
    with pytest.raises(IdentifierError):
        parse_paper_ref("doi:not-a-doi")
    with pytest.raises(IdentifierError):
        parse_paper_ref("CorpusId:abc")
    with pytest.raises(IdentifierError):
        parse_paper_ref("   ")


def test_normalize_title_for_dedup():
    assert normalize_title("Café: NeRF—in the Wild!") == normalize_title("cafe nerf in the wild")
