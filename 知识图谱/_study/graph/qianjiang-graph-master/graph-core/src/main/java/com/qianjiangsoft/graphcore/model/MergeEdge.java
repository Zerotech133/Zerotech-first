package com.qianjiangsoft.graphcore.model;

import lombok.AllArgsConstructor;
import lombok.Data;

import java.util.Map;
import java.util.Objects;

/**
 * @author marshallwu
 * @date 2022/2/23 16:37
 */
@AllArgsConstructor
@Data
public class MergeEdge {
    Vertex source;
    Vertex destination;
    EdgeType type;

    /** 强度 */
    Double strength;

    /** 次数 */
    Long count;

    // 卡片(key,value)
    Map<String, String> cardList;

    @Override
    public boolean equals(Object o) {
        if (this == o) return true;
        if (o == null || getClass() != o.getClass()) return false;
        MergeEdge mergeEdge = (MergeEdge) o;
        return getSource().equals(mergeEdge.getSource()) && getDestination().equals(mergeEdge.getDestination()) && getType().equals(mergeEdge.getType());
    }

    @Override
    public int hashCode() {
        return Objects.hash(getSource(), getDestination(), getType());
    }
}
