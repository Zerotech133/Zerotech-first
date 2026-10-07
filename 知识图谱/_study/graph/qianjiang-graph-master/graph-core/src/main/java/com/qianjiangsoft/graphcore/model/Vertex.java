package com.qianjiangsoft.graphcore.model;

import com.baomidou.mybatisplus.annotation.TableId;
import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.Map;
import java.util.Objects;

/**
 * @author marshallwu
 * @date 2022/2/23 16:34
 */
@Data
@AllArgsConstructor
@NoArgsConstructor
public class Vertex {

    @TableId
    String id;

    String name;

    Map<String, Object> propertyList;

    // 卡片(key,value)
    Map<String, String> cardList;

    Tag tag;

    //点的统计信息，统计查询使用
    VertexStatisticsInfo vertexStatisticsInfo;

    public Vertex(String id, String name, Map<String, Object> propertyList, Tag tag) {
        this.id = id;
        this.name = name;
        this.propertyList = propertyList;
        this.tag = tag;
    }

    @Override
    public boolean equals(Object o) {
        if (this == o) {
            return true;
        }
        if (o == null || getClass() != o.getClass()) {
            return false;
        }
        Vertex vertex = (Vertex) o;
        return Objects.equals(getId(), vertex.getId());
    }

    @Override
    public int hashCode() {
        return Objects.hash(getId());
    }
}
