import unittest

from solution import (OTHER_GROUP, collation_key, first_letter, group_by_first_letter,
                      naive_first_letter, naive_sort_names, sort_names)

CONTACTS = ["Zoe Adams", "anna Berg", "Émile Durand", "Eva Lind", "Øster Holm", "Oscar Wilde",
            "émile durand", "Emile Durand", "zack Young"]


class ReproduceProblem(unittest.TestCase):
    def test_lowercase_names_sort_after_every_uppercase_name(self):
        ordered = naive_sort_names(["anna Berg", "Zoe Adams"])
        self.assertEqual(ordered, ["Zoe Adams", "anna Berg"])

    def test_accented_names_sort_after_z(self):
        ordered = naive_sort_names(["Émile Durand", "Zoe Adams", "Eva Lind"])
        self.assertEqual(ordered[-1], "Émile Durand")

    def test_accented_name_is_missing_from_the_e_section(self):
        self.assertEqual(naive_first_letter("Émile Durand"), "É")


class FixAlphabeticalOrder(unittest.TestCase):
    def test_case_does_not_decide_the_order(self):
        self.assertEqual(sort_names(["Zoe Adams", "anna Berg"]), ["anna Berg", "Zoe Adams"])

    def test_accented_name_sorts_with_its_base_letter(self):
        ordered = sort_names(["Zoe Adams", "Émile Durand", "Eva Lind"])
        self.assertEqual(ordered, ["Émile Durand", "Eva Lind", "Zoe Adams"])

    def test_letters_without_decomposition_are_folded(self):
        ordered = sort_names(["Oscar Wilde", "Øster Holm", "Nora Ek"])
        self.assertEqual(ordered, ["Nora Ek", "Oscar Wilde", "Øster Holm"])

    def test_sharp_s_sorts_like_ss(self):
        ordered = sort_names(["Strauss", "Straße", "Strand"])
        self.assertEqual(ordered, ["Strand", "Straße", "Strauss"])

    def test_names_differing_only_by_accent_or_case_have_a_fixed_order(self):
        ordered = sort_names(["Émile Durand", "émile durand", "Emile Durand"])
        self.assertEqual(ordered, ["Emile Durand", "Émile Durand", "émile durand"])

    def test_order_does_not_depend_on_input_order(self):
        self.assertEqual(sort_names(CONTACTS), sort_names(list(reversed(CONTACTS))))

    def test_leading_spaces_do_not_move_a_name_to_the_top(self):
        self.assertEqual(sort_names(["  Zoe", "Adam"]), ["Adam", "  Zoe"])

    def test_precomposed_and_decomposed_forms_get_the_same_primary_key(self):
        precomposed = "Émile"
        decomposed = "Émile"
        self.assertEqual(collation_key(precomposed)[0], collation_key(decomposed)[0])


class FixDirectoryIndex(unittest.TestCase):
    def test_accented_name_is_listed_under_its_base_letter(self):
        self.assertEqual(first_letter("Émile Durand"), "E")
        self.assertEqual(first_letter("Øster Holm"), "O")

    def test_non_latin_and_digit_names_go_to_the_other_group(self):
        self.assertEqual(first_letter("李雷"), OTHER_GROUP)
        self.assertEqual(first_letter("3M Support"), OTHER_GROUP)
        self.assertEqual(first_letter(""), OTHER_GROUP)

    def test_sections_contain_every_name_once(self):
        groups = group_by_first_letter(CONTACTS)
        flattened = [name for section in groups.values() for name in section]
        self.assertEqual(sorted(flattened), sorted(CONTACTS))
        self.assertEqual(list(groups), ["A", "E", "O", "Z"])


if __name__ == "__main__":
    unittest.main()
