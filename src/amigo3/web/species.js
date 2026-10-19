/* Shared labels; available species come from the pinned source manifest.
   The server prepends SPECIES_LABELS from config/sources.yaml (species keys and aliases). */
const AmigoSpecies = {
  label(value) {
    return SPECIES_LABELS[value] || value;
  },
  keys() {
    return Object.keys(SPECIES_LABELS);
  },
  loaded(manifest) {
    return [...new Set(Object.values(manifest.sources || {}).filter(s=>s.kind==='gaf').map(s=>s.species))].sort();
  }
};
