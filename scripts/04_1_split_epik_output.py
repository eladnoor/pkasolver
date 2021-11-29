from rdkit import Chem
from pkasolver.chem import create_conjugate
import argparse
import gzip
from rdkit.Chem.AllChem import Compute2DCoords


PH = 7.4


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", help="input filename")
    parser.add_argument("--output", help="output filename")
    args = parser.parse_args()
    input_zipped = False
    print("inputfile:", args.input)
    print("outputfile:", args.output)

    # test if it's gzipped
    with gzip.open(args.input, "r") as fh:
        try:
            fh.read(1)
            input_zipped = True
        except gzip.BadGzipFile:
            input_zipped = False

    if input_zipped:
        with gzip.open(args.input, "r") as fh:
            suppl = Chem.ForwardSDMolSupplier(fh, removeHs=True)
            processing(suppl, args)
    else:
        with open(args.input, "rb") as fh:
            suppl = Chem.ForwardSDMolSupplier(fh, removeHs=True)
            processing(suppl, args)


def processing(suppl, args):

    nr_of_skipped_mols = 0
    with gzip.open(args.output, "wt+") as sdf_zip:
        with Chem.SDWriter(sdf_zip) as writer:
            for nr_of_mols, mol in enumerate(suppl):
                if not mol:
                    continue
                Compute2DCoords(mol)
                skipping_bases = 0
                skipping_acids = 0
                try:
                    props = mol.GetPropsAsDict()
                except AttributeError as e:
                    # this mol has no pka value
                    nr_of_skipped_mols += 1
                    print(e)
                    continue
                nr_of_protonation_states = len(
                    [s for s in props.keys() if "r_epik_pKa" in s]
                )
                pkas = []
                for i in range(nr_of_protonation_states):
                    pkas.append(
                        (
                            float(props[f"r_epik_pKa_{i+1}"]),
                            int(props[f"i_epik_pKa_atom_{i+1}"]) - 1,
                            props[f"chembl_id"],
                        )
                    )

                # calculate number of acidic and basic pka values
                nr_of_acids = sum(pka[0] <= PH for pka in pkas)
                nr_of_bases = sum(pka[0] > PH for pka in pkas)
                assert nr_of_acids + nr_of_bases == len(pkas)

                acidic_mols_properties = [
                    mol_pka for mol_pka in pkas if mol_pka[0] <= PH
                ]
                basic_mols_properties = [mol_pka for mol_pka in pkas if mol_pka[0] > PH]

                # print(f"acidic prop:{acidic_mols_properties}")
                # print(f"basic prop: {basic_mols_properties}")
                if len(acidic_mols_properties) != nr_of_acids:
                    raise RuntimeError(f"{acidic_mols_properties=}, {nr_of_acids=}")
                if len(basic_mols_properties) != nr_of_bases:
                    raise RuntimeError(f"{basic_mols_properties=}, {nr_of_bases=}")

                # clear porps
                for prop in props.keys():
                    mol.ClearProp(prop)

                # add neutral mol as first acidic mol
                acidic_mols = [mol]
                for idx, acid_prop in enumerate(
                    reversed(acidic_mols_properties)
                ):  # list must be iterated in reverse, in order to protonated the strongest conjugate base first
                    if (
                        skipping_acids == 0
                    ):  # if a acid was skipped, all further acids are skipped
                        try:
                            new_mol = create_conjugate(
                                acidic_mols[-1], acid_prop[1], acid_prop[0], pH=PH
                            )
                            # Chem.SanitizeMol(new_mol)

                        except Exception as e:
                            print(f"Error at molecule number {nr_of_mols}")
                            print(e)
                            print(acid_prop)
                            print(acidic_mols_properties)
                            print(Chem.MolToMolBlock(mol))
                            skipping_acids += 1
                            nr_of_skipped_mols += 1
                            continue  # continue instead of break, will not enter this routine gain since skipping_acids != 0

                        new_mol.SetProp(f"ID", str(acid_prop[2]))
                        new_mol.SetProp(f"pKa", str(acid_prop[0]))
                        new_mol.SetProp(f"marvin_pKa", str(acid_prop[0]))
                        new_mol.SetProp(f"marvin_atom", str(acid_prop[1]))
                        new_mol.SetProp(f"pka_number", f"acid_{idx + 1}")
                        # add current mol to list of acidic mol. for next
                        # lower pKa value, this mol is starting structure
                        acidic_mols.append(new_mol)

                    else:
                        skipping_acids += 1

                # same workflow for basic mols
                basic_mols = [mol]
                for idx, basic_prop in enumerate(basic_mols_properties):

                    if (
                        skipping_bases == 0
                    ):  # if a base was skipped, all further bases are skipped
                        new_mol = basic_mols[-1]
                        new_mol.SetProp(f"ID", str(basic_prop[2]))
                        new_mol.SetProp(f"pKa", str(basic_prop[0]))
                        new_mol.SetProp(f"marvin_pKa", str(basic_prop[0]))
                        new_mol.SetProp(f"marvin_atom", str(basic_prop[1]))
                        new_mol.SetProp(f"pka_number", f"base_{idx + 1}")

                        try:
                            basic_mols.append(
                                create_conjugate(
                                    basic_mols[-1], basic_prop[1], basic_prop[0], pH=PH
                                )
                            )
                            # Chem.SanitizeMol(new_mol)
                        except Exception as e:
                            # in case error occurs new_mol is not in basic list
                            print(f"Error at molecule number {nr_of_mols}")
                            print(e)
                            print(basic_prop)
                            print(basic_mols_properties)
                            print(Chem.MolToMolBlock(mol))
                            skipping_bases += 1
                            nr_of_skipped_mols += 1
                    else:
                        skipping_bases += 1

                # combine basic and acidic mols, skip neutral mol for acids
                # bases start with neutral mol and leave out last entry so for every pKa reaction
                # only the protonated participant is included
                mols = acidic_mols[1:] + basic_mols[:-1]
                assert (
                    len(mols)
                    == len(acidic_mols_properties)
                    - skipping_acids
                    + len(basic_mols_properties)
                    - skipping_bases
                )

                for mol in mols:
                    writer.write(mol)

    print(f"finished splitting {nr_of_mols} molecules")
    print(f"skipped mols: {nr_of_skipped_mols}")


if __name__ == "__main__":
    main()
