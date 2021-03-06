from django.db import connection
from models import QuestRating, SKILLS
from herobase.models import Quest
from signals import like, apply, participate, participate_plus

__all__ = ['recommend', 'recommend_local', 'recommend_remote', 'like', 'apply',
        'participate', 'participate_plus']


def filter_by_location(queryset, latitude, longitude, radius_km=50):
    dlat = (1/110.0)* radius_km
    dlon = (1/70.0) * radius_km
    distance = """sqrt(pow(110 * (latitude - %s), 2) + pow(70 * (longitude - %s), 2))""" % (latitude, longitude)
    return (queryset
            .extra(select={'distance': distance})
            .filter(latitude__range=(latitude - dlat, latitude + dlat),
                    longitude__range=(longitude - dlon, longitude + dlon)))

def recommend_top(user, n, fields=('title', 'description', 'state', 'location',
        'remote', 'latitude', 'longitude'), queryset=None, order_by=None):
    quests = recommend(user, fields=fields, queryset=queryset)[:3*n]
    return None

def recommend(user, fields=('title', 'description', 'state', 'location', 'remote', 'latitude', 'longitude'),
        queryset=None, order_by=None):
    if queryset is None:
        queryset = Quest.objects.open()
    return queryset

    remote = queryset.filter(remote=True)
    if user.get_profile().has_location:
        local = filter_by_location(queryset.filter(remote=False),
            user.get_profile().latitude,
            user.get_profile().longitude)
        queryset = local | remote
        local = True
    else:
        queryset = remote
        local = False

    return recommend_for_user(user, fields=fields, queryset=queryset,
            order_by=order_by, local=local)

def recommend_remote(user, fields=('title', 'description', 'state'),
        queryset=None, order_by=None):
    if queryset is None:
        queryset = Quest.objects.open()

    queryset = queryset.filter(remote=True)
    return queryset
    return recommend_for_user(user, fields=fields, queryset=queryset,
            order_by=order_by, local=False)

def recommend_local(user, fields=('title', 'description', 'state'),
        queryset=None, order_by=None):
    if queryset is None:
        queryset = Quest.objects.open()

    queryset = queryset.filter(remote=False)
    return queryset
    return recommend_for_user(user, fields=fields, queryset=queryset,
            order_by=order_by, local=True)


def recommend_for_user(user, fields=('title', 'description', 'state'),
        local=False, queryset=None, order_by=None):
    if queryset is None:
        queryset = Quest.objects.open()
    return queryset
    order_fields = ['-weight']
    if order_by:
        order_fields.extend(order_by)
    
    result_fields = ['weight', 'profile__average', 'id', 'pk', 'remote']
    if local:
        result_fields.append('distance')
    if fields:
        result_fields = list(fields) + result_fields

    up = user.combined_profile
    up_deltas = {}
    up_average = up.average
    for skill in SKILLS:
        up_deltas[skill] = getattr(up, skill) - up_average

    up_root_sum_of_squares = sum(w**2 for w in up_deltas.values()) ** 0.5
    if up_root_sum_of_squares == 0:
        sql = '1'
    else:
        denominator = []
        for skill in SKILLS:
            denominator.append('(%s * delta_%s)' % (up_deltas[skill], skill))
        denominator_sql = '(%s)' % (' + '.join(denominator))

        numerator_sql = '%s * root_sum_of_squares' % up_root_sum_of_squares

        sql = '(%s) / (%s)' % (denominator_sql, numerator_sql) # fixme DIV BY ZERO
        if user.get_profile().has_location:
            sql += '+ COALESCE(pow(2.718, - (sqrt(pow(110 * (latitude - %s), 2) + pow(70 * (longitude - %s), 2)))/10), 0)' % (user.get_profile().latitude, user.get_profile().longitude)
        sql += '+ (random()/5)'

    cursor = connection.cursor()
    if cursor.db.vendor != 'sqlite':
        cursor.execute("SELECT SETSEED(%s)" % (1/user.pk))
    cursor.close()

    recommended = (queryset
            .select_related('profile')
            .extra(select={'weight': sql})
            .order_by(*order_fields))
           # .values(*result_fields))
    return recommended

