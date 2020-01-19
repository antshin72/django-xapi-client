from importlib import import_module
import uuid
from tincan import (
    RemoteLRS,
    Statement,
    Agent, AgentAccount,
    Verb,
    Activity,
    Context,
    LanguageMap,
    ActivityDefinition,
    StateDocument,
)

from django.core.exceptions import ImproperlyConfigured
from django.conf import settings

vocabulary_module = import_module(settings.XAPI_VOCABULARIES_MODULE)
xapi_verbs = getattr(vocabulary_module, 'xapi_verbs')
xapi_activities = getattr(vocabulary_module, 'xapi_activities')
XAPI_PLATFORM = settings.XAPI_DEFAULT_PLATFORM
XAPI_LANGUAGE = settings.LANGUAGE_CODE

try:
    from datatrans.utils import get_current_language
except:
    from django.utils import translation

    def get_default_language():
        """
        Get the source language code if specified, else just the default language code.
        """
        lang = getattr(settings, 'SOURCE_LANGUAGE_CODE', settings.LANGUAGE_CODE)
        default = [l[0] for l in settings.LANGUAGES if l[0] == lang]
        if len(default) == 0:
            # when not found, take first part ('en' instead of 'en-us')
            lang = lang.split('-')[0]
            default = [l[0] for l in settings.LANGUAGES if l[0] == lang]
        if len(default) == 0:
            raise ImproperlyConfigured("The [SOURCE_]LANGUAGE_CODE '%s' is not found in your LANGUAGES setting." % lang)
        return default[0]

    def get_current_language():
        """
        Get the current language
        """
        lang = translation.get_language() or 'en'
        current = [l[0] for l in settings.LANGUAGES if l[0] == lang]
        if len(current) == 0:
            lang = lang.split('-')[0]
            current = [l[0] for l in settings.LANGUAGES if l[0] == lang]
        if len(current) == 0:
            # Fallback to default language code
            return get_default_language()
        return current[0]

def get_name(obj):
    return hasattr(obj, '__str__') and obj.__str__() or ''

def get_description(obj):
    description = ''
    if hasattr(obj, 'description'):
        description = obj.description
    if hasattr(obj, 'short'):
        description = obj.short
    return description

def get_language(obj):
    original_language = hasattr(obj, 'original_language') and obj.original_language or None;
    current_language = get_current_language()
    if original_language:
        if current_language == original_language:
            return original_language
    return original_language or current_language

def get_object_id(request, object):
    action = object and object.__class__.__name__ or None
    if hasattr(object, 'absolute_url'):
        location = object.absolute_url()
    elif hasattr(object, 'get_absolute_url'):
        location = object.get_absolute_url()
    else:
        location = '/%s/%d/' % (action, object.id)
    if request:
        object_id = request.build_absolute_uri(location)
    elif not location.count('http'):
        object_id = '%s://%s%s' % (settings.PROTOCOL, settings.HOST, location)
    object_id = object_id.replace('cs.up2university.eu', 'www.commonspaces.eu')
    return object_id

def get_context_parent(request, target):
    target_type = target.__class__.__name__
    return {
       'objectType': 'Activity',
       'id': get_object_id(request, target),
       'definition': {'type': xapi_activities[target_type]['type'], 'name': {'en': get_name(target)}}
    }

def get_context_grouping(request, target):
    target_type = target.__class__.__name__
    return {
       'objectType': 'Activity',
       'id': get_object_id(request, target),
       'definition': {'type': xapi_activities[target_type]['type'], 'name': {get_language(target): get_name(target)}}
    }

def put_statement(request, user, verb, object, target, language=XAPI_LANGUAGE):

    # construct an LRS
    lrs = RemoteLRS(
        version = settings.LRS_VERSION,
        endpoint = settings.LRS_ENDPOINT,
        auth = settings.LRS_AUTH,
    )

    # construct the actor of the statement
    # IMPORTANT - account is OK but cannot coexist with mbox or other way of uniquely identifying the actor
    actor = Agent(
        name=user.get_display_name(),
        mbox='mailto:%s' % user.email,
        # account=AgentAccount(name=str(user.pk), home_page='https://www.commonspaces.eu')
    )

    # construct the verb of the statement
    verb = Verb(
        id=xapi_verbs[verb]['id'],
        display=LanguageMap(**xapi_verbs[verb]['display']),
    )

    action = object.__class__.__name__
    activity_type = xapi_activities[action]['type']
    object_id = get_object_id(request, object) # 190307 GT: defined get_object_id
    object_name = get_name(object) # 190307 GT: defined get_name
    object_description = get_description(object)
    object_language = get_language(object)
    activity_definition = ActivityDefinition(
         name=LanguageMap(**{object_language: object_name}),
         description=object_description and LanguageMap(**{object_language: object_description}) or None,
         type=activity_type,                                        
    )

    # construct the object of the statement
    object = Activity(
        id=object_id,
        definition=activity_definition,
    )

    context = {'platform': XAPI_PLATFORM, 'language': get_current_language()}
    """ 190308 GT: would produce the exception "Object of type 'UUID' is not JSON serializable" in getting the response
    if request:
        context['registration'] = str(get_registration(request))
    """
    if target:
        target_type = target.__class__.__name__
        context_activities = {}
        if target_type in ['Folder', 'Forum', 'LearningPath']:
            context_activities['parent'] = {
               'objectType': 'Activity',
               'id': get_object_id(request, target),
               'definition': {'type': xapi_activities[target_type]['type'], 'name': {'en': get_name(target)}}
            }
            if target_type == 'Folder':
                project = target.get_project()
                if project:
                    context_activities['grouping'] = get_context_grouping(request, project)
            elif target_type == 'Forum':
                project = target.forum_get_project()
                if project:
                    context_activities['grouping'] = get_context_grouping(request, project)
            elif target_type == 'LearningPath':
                if target.project:
                    context_activities['grouping'] = get_context_grouping(request, target.project)
        elif target_type == 'Project':
            context_activities['grouping'] = get_context_grouping(request, target)
        if context_activities:
            context['context_activities'] = context_activities
    context = Context(**context)

    # construct the actual statement
    statement = Statement(
        actor=actor,
        verb=verb,
        object=object,
        context=context,
    )
    # print (statement.to_json(lrs.version))

    # save our statement to the remote_lrs and store the response in 'response'
    response = lrs.save_statement(statement)

    if not response:
        raise ValueError("statement failed to save")
    if not response.success:
        print ("...response:")
        print ("...content:", response.content)
        print ("...data:", response.data)
        raise ValueError("response was unsuccessful")

    # retrieve our statement from the remote_lrs using the id returned in the response
    response = lrs.retrieve_statement(response.content.id)

    if not response.success:
        raise ValueError("statement could not be retrieved")

    return response.success