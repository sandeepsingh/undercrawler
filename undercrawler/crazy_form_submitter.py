import logging
import random
import string

from scrapy.http import FormRequest
from scrapy.http.request.form import _get_inputs as get_form_data


logger = logging.getLogger(__name__)

SEARCH_TERMS = list(string.ascii_lowercase) + list('123456789 *%.?')


def search_form_requests(url, form, meta, extra_search_terms=None,
                         request_kwargs=None):
    ''' yield search requests, using default search terms and
    extra_search_terms, also randomly refining search if there are such
    options in the form.
    '''
    refinement_options = [False]
    if not any(input_type == 'search query'
               for input_type in meta['fields'].values()):
        return
    n_target_inputs = sum(
        input_type == 'search query' or
        _is_refinement_input(input_type, form.inputs[input_name])
        for input_name, input_type in meta['fields'].items())
    assert n_target_inputs >= 0
    # 2 and 4 here are just some values that feel right, need tuning
    refinement_options.append([True] * 2 * min(2, n_target_inputs))

    request_kwargs = request_kwargs or {}
    extra_search_terms = set(extra_search_terms or [])
    main_search_terms = set(SEARCH_TERMS)
    for search_term in (main_search_terms | extra_search_terms):
        for do_random_refinement in refinement_options:
            formdata = _fill_search_form(
                search_term, form, meta, do_random_refinement)
            if formdata is not None:
                priority = -3 if do_random_refinement else -1
                if search_term not in main_search_terms:
                    priority = random.randint(-100, priority)
                logger.debug(
                    'Scheduled search: "%s" at %s with priority %d%s',
                    search_term, url, priority,
                    ' with random refinement' if do_random_refinement else '')
                yield FormRequest(
                    url=url,
                    formdata=formdata,
                    method=form.method,
                    priority=priority,
                    **request_kwargs)


def _fill_search_form(search_term, form, meta, do_random_refinement=False):
    additional_formdata = {}
    search_fields = []
    for input_name, input_type in meta['fields'].items():
        input_el = form.inputs[input_name]
        if input_type == 'search query':
            search_fields.append(input_name)
        elif do_random_refinement and \
                _is_refinement_input(input_type, input_el):
            if input_el.type == 'checkbox' and random.random() > 0.5:
                additional_formdata[input_name] = 'on'
    additional_formdata[random.choice(search_fields)] = search_term
    return get_form_data(form, additional_formdata, None, None, None)


def _is_refinement_input(input_type, input_el):
    return (input_type == 'search category / refinement' and
            getattr(input_el, 'type', None) in ['checkbox'])
