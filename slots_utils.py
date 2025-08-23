def get_consecutive_slots_join(slot_list, slot_list2, how='diff'):
    """
    Returns the difference/join/union of two slot lists, based on the consecutive slots that make part of such lists.
    How = ['union', 'join', 'diff'] -> if how=='diff' will return the two lists that come from l1-l2 and l2-l1 respectively.
    if how=='join' will return the list of the slots that belong to both lists.
    if how=='union' will return the sorted_list (based on start_time) from first slot till last slot
    """

    def __map_to_output__(l1_diff_at_start, l1_diff_at_end, l2_diff_at_start, l2_diff_at_end, l_join, how):
        if how=='union':
            return l1_diff_at_start + l2_diff_at_start + l_join + l1_diff_at_end + l2_diff_at_end
        if how=='intersect':
            return l_join
        if how=='diff':
            return l1_diff_at_start + l1_diff_at_end, l2_diff_at_start + l2_diff_at_end
        
    if how not in ['union', 'intersect', 'diff']:
        raise ValueError("How must be one of: ['union', 'intersect', 'diff']")
    if not slot_list or not slot_list2:
        return __map_to_output__(l1_diff_at_start=slot_list, l2_diff_at_start=slot_list2, l_join=[], how=how,
                                l1_diff_at_end=[], l2_diff_at_end=[])
    start_times = list(map(lambda x: x.start_time, slot_list))
    start_times2 = list(map(lambda x: x.start_time, slot_list2))

    if start_times[0]>start_times2[-1] or start_times[-1]<start_times2[0]:
        return __map_to_output__(l1_diff_at_start=slot_list, l2_diff_at_start=slot_list2, l_join=[], how=how,
                                l1_diff_at_end=[], l2_diff_at_end=[])

    idx_in_l1 = 0
    idx_in_l2 = bisect_left(start_times2, start_times[0])
    starting_element = start_times[0]
    
    if start_times2[idx_in_l2]!=start_times[0]:
        starting_element = start_times2[0]
        idx_in_l1 = bisect_left(start_times, start_times2[0]) 
        idx_in_l2 = 0

    len_common = min(len(start_times) - idx_in_l1, len(start_times2) - idx_in_l2)
    l_intersect = slot_list[idx_in_l1:idx_in_l1+len_common]
    if how=='intersect':
        return l_intersect
    l1_diff_at_start = slot_list[:idx_in_l1] ##at most one of l1_diff or l2_diff will have elements
    l2_diff_at_start = slot_list2[:idx_in_l2]
    
    # Step 5. Collect trailing differences (if lists end unevenly)
    l1_diff_at_end = slot_list[idx_in_l1 + len_common:]
    #l1_diff.extend(l1_diff_at_end)
    
    l2_diff_at_end = slot_list2[idx_in_l2 + len_common:]
    #l2_diff.extend(l2_diff_at_end)
    
    return __map_to_output__(l1_diff_at_start=l1_diff_at_start, 
                             l1_diff_at_end=l1_diff_at_end, 
                             l2_diff_at_start=l2_diff_at_start, 
                             l2_diff_at_end=l2_diff_at_end,
                             l_join=l_intersect, how=how)