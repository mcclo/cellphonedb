import pandas as pd

from cellphonedb.database.Repository import Repository
from cellphonedb.core.models.complex.db_model_complex import Complex
from cellphonedb.core.models.complex_composition.db_model_complex_composition import ComplexComposition
from cellphonedb.core.models.multidata import properties_multidata
from cellphonedb.core.models.multidata.db_model_multidata import Multidata
from cellphonedb.core.models.protein.db_model_protein import Protein
from cellphonedb.core.utils import filters


class ComplexRepository(Repository):
    name = 'complex'

    def get_all(self) -> pd.DataFrame:
        query = self.database_manager.database.session.query(Complex)
        result = pd.read_sql(query.statement, self.database_manager.database.engine)

        return result

    def get_all_expanded(self) -> pd.DataFrame:
        query = self.database_manager.database.session.query(Complex, Multidata).join(Multidata)
        result = pd.read_sql(query.statement, self.database_manager.database.engine)

        return result

    def get_all_compositions(self) -> pd.DataFrame:
        query = self.database_manager.database.session.query(ComplexComposition)
        result = pd.read_sql(query.statement, self.database_manager.database.engine)

        return result

    def get_all_compositions_expanded(self, suffixes=None) -> pd.DataFrame:
        if not suffixes:
            suffixes = ['_complex', '_protein']

        query = self.database_manager.database.session.query(ComplexComposition)
        complex_composition = pd.read_sql(query.statement, self.database_manager.database.engine)

        multidata_query = self.database_manager.database.session.query(Multidata)
        multidatas = pd.read_sql(multidata_query.statement, self.database_manager.database.engine)

        complex_composition_expanded = pd.merge(complex_composition, multidatas, left_on='complex_multidata_id',
                                                right_on='id_multidata')

        complex_composition_expanded = pd.merge(complex_composition_expanded, multidatas,
                                                left_on='protein_multidata_id',
                                                right_on='id_multidata', suffixes=suffixes)

        return complex_composition_expanded

    def get_complex_by_multidatas(self, multidatas: pd.DataFrame, all_proteins_expressed: bool = True) -> pd.DataFrame:
        complex_composition = self.get_all_compositions()

        multidatas_ids = multidatas['id_multidata'].to_frame()
        complex_composition_merged = pd.merge(complex_composition, multidatas_ids, left_on='protein_multidata_id',
                                              right_on='id_multidata')

        if complex_composition_merged.empty:
            return complex_composition_merged

        def all_protein_expressed(complex):
            number_proteins_in_counts = len(
                complex_composition_merged[
                    complex_composition_merged['complex_multidata_id'] == complex['complex_multidata_id']])

            if number_proteins_in_counts < complex['total_protein']:
                return False

            return True

        if all_proteins_expressed:
            complex_composition_merged = complex_composition_merged[
                complex_composition_merged.apply(all_protein_expressed, axis=1)]

        complexes = self.get_all_expanded()
        complex_composition_merged = pd.merge(complex_composition_merged, complexes,
                                              left_on='complex_multidata_id',
                                              right_on='id_multidata',
                                              suffixes=['_protein', ''])

        complex_composition_merged.drop_duplicates(['complex_multidata_id'], inplace=True)

        return complex_composition_merged

    # TODO: it needs to be refactored
    def add(self, complexes):
        """
        Uploads complex data from csv.

        - Creates new complexes in Multidata table
        - Creates reference in Complex table
        - Creates complex composition to define complexes.
        :param complex_file:
        :return:
        """
        existing_complexes = self.database_manager.database.session.query(Multidata.name).all()
        existing_complexes = [c[0] for c in existing_complexes]
        proteins = self.database_manager.database.session.query(Multidata.name, Multidata.id_multidata).join(
            Protein).all()
        proteins = {p[0]: p[1] for p in proteins}
        # Read in complexes
        complexes.dropna(axis=1, inplace=True, how='all')
        complexes.rename(index=str, columns={'complex_name': 'name'}, inplace=True)

        # Get complex composition info
        complete_indices = []
        incomplete_indices = []
        missing_proteins = []
        complex_map = {}
        for index, row in complexes.iterrows():
            missing = False
            protein_id_list = []
            for protein in ['protein_1', 'protein_2',
                            'protein_3', 'protein_4']:
                if not pd.isnull(row[protein]):
                    protein_id = proteins.get(row[protein])
                    if protein_id is None:
                        missing = True
                        missing_proteins.append(row[protein])
                    else:
                        protein_id_list.append(protein_id)
            if not missing:
                complex_map[row['name']] = protein_id_list
                complete_indices.append(int(index))
            else:
                incomplete_indices.append(index)

        if len(incomplete_indices) > 0:
            print('MISSING PROTEINS:')
            for protein in missing_proteins:
                print(protein)

            print('COMEPLEXES WITH MISSING PROTEINS:')
            print(complexes.iloc[incomplete_indices, :]['name'])

        # Insert complexes
        if not complexes.empty:
            # Remove unwanted columns
            removal_columns = list(
                [x for x in complexes.columns if 'protein_' in x or 'Name_' in x or 'Unnamed' in x])
            # removal_columns += ['comments']
            complexes.drop(removal_columns, axis=1, inplace=True)

            # Remove rows with missing complexes
            complexes = complexes.iloc[complete_indices, :]

            # Convert ints to bool
            bools = ['receptor', 'receptor_highlight', 'adhesion', 'other',
                     'transporter', 'secreted_highlight', 'transmembrane', 'secretion', 'peripheral',
                     'iuhpar_ligand',
                     'extracellular', 'cytoplasm']
            complexes[bools] = complexes[bools].astype(bool)

            # Drop existing complexes
            complexes = complexes[complexes['name'].apply(
                lambda x: x not in existing_complexes)]

            multidata_df = filters.remove_not_defined_columns(complexes.copy(),
                                                              self.database_manager.get_column_table_names(
                                                                  'multidata'))

            multidata_df = self._add_complex_optimitzations(multidata_df)
            multidata_df.to_sql(name='multidata', if_exists='append', con=self.database_manager.database.engine,
                                index=False)

        # Now find id's of new complex rows
        new_complexes = self.database_manager.database.session.query(Multidata.name, Multidata.id_multidata).all()
        new_complexes = {c[0]: c[1] for c in new_complexes}

        # Build set of complexes
        complex_set = []
        complex_table = []
        for complex_name in complex_map:
            complex_id = new_complexes[complex_name]
            for protein_id in complex_map[complex_name]:
                complex_set.append((complex_id, protein_id, len(complex_map[complex_name])))
            complex_table.append({'complex_multidata_id': complex_id, 'name': complex_name})

        # Insert complex composition
        complex_set_df = pd.DataFrame(complex_set,
                                      columns=['complex_multidata_id', 'protein_multidata_id', 'total_protein'])

        complex_table_df = pd.DataFrame(complex_table)
        complex_table_df = pd.merge(complex_table_df, complexes, on='name')

        filters.remove_not_defined_columns(complex_table_df,
                                           self.database_manager.get_column_table_names('complex'))

        complex_table_df.to_sql(
            name='complex', if_exists='append',
            con=self.database_manager.database.engine, index=False)

        complex_set_df.to_sql(
            name='complex_composition', if_exists='append',
            con=self.database_manager.database.engine, index=False)

    def _add_complex_optimitzations(self, multidatas):
        multidatas['is_complex'] = True
        multidatas['is_cellphone_receptor'] = multidatas.apply(
            lambda protein: properties_multidata.is_receptor(protein),
            axis=1)

        return multidatas