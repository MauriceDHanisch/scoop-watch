# Project: OrbitAll, a unified quantum-mechanical representation for all molecular systems

> Example project for scoop-watch. It is based on the published paper
> **OrbitAll** (Kang et al., arXiv:2507.03853, https://arxiv.org/abs/2507.03853)
> and is included with attribution as a worked example. Replace it with a
> description of your own project.

The project applies machine learning to quantum chemistry and contributes a
representation that works across the full range of molecular systems, not only
neutral closed-shell molecules. It has two sub-themes; a competing paper would
overlap with one of them.

## Theme: Property prediction across electronic states

Most machine-learning models for molecular property prediction are trained and
evaluated on neutral, closed-shell molecules. Charged species (ions),
open-shell systems (radicals with unpaired electrons), and solvated molecules
are common in real chemistry but break models that assume a single neutral
closed-shell regime.

OrbitAll predicts quantum-mechanical properties (ground-state energies, dipole
moments, and related electronic-structure quantities) with one model that
accepts arbitrary total charge, spin multiplicity, and solvation environment.
It reaches chemical accuracy with roughly ten times less training data than
comparable models, and runs three to four orders of magnitude faster than
density functional theory.

A paper overlaps with this theme if it predicts quantum-mechanical properties
for charged, open-shell, or solvated molecules, or proposes a single model
spanning these electronic states.

## Theme: Spin-polarized orbital-feature representation

This is the architectural contribution that makes the application possible.

OrbitAll builds its node features from a low-cost quantum-mechanical
calculation rather than from atom types and coordinates alone. The orbital
features are spin-polarized: spin-up and spin-down occupations are carried
separately, which is what lets a single model represent open-shell systems and
net charge. These features feed an SE(3)-equivariant graph neural network, so
predictions respect rotational and translational symmetry, and the model
extrapolates to molecules substantially larger than those seen in training.

A paper overlaps with this theme if it uses orbital or mean-field features as
graph-network inputs, builds spin-resolved molecular representations, or targets
size extrapolation with an equivariant network.
