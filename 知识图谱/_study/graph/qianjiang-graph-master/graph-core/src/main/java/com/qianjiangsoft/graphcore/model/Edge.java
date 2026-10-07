package com.qianjiangsoft.graphcore.model;

import lombok.AllArgsConstructor;
import lombok.Data;

import java.util.Date;
import java.util.Map;

/**
 * @author marshallwu
 * @date 2022/2/23 16:37
 */
@AllArgsConstructor
@Data
public class Edge {

    String id;
    Vertex source;
    Vertex destination;
    EdgeType type;

    /** 强度 */
    Double strength;

    /** 时间 */
    Date time;


    Map<String, Object> propertyList;

    // 卡片(key,value)
    Map<String, String> cardList;

    public Edge(String id, Vertex source, Vertex destination, EdgeType type, Map<String, Object> propertyList) {
        this.id = id;
        this.source = source;
        this.destination = destination;
        this.type = type;
        this.propertyList = propertyList;
    }

    public String getId() {
        return id;
    }

    public void setId(String id) {
        this.id = id;
    }

    public Vertex getSource() {
        return source;
    }

    public void setSource(Vertex source) {
        this.source = source;
    }

    public Vertex getDestination() {
        return destination;
    }

    public void setDestination(Vertex destination) {
        this.destination = destination;
    }

    public EdgeType getType() {
        return type;
    }

    @Override
    public String toString() {
        return "Edge{" +
                "id='" + id + '\'' +
                ", source=" + source +
                ", destination=" + destination +
                ", type=" + type +
                ", strength=" + strength +
                ", time=" + time +
                ", propertyList=" + propertyList +
                '}';
    }

    public void setType(EdgeType type) {
        this.type = type;
    }

    public Double getStrength() {
        return strength;
    }

    public void setStrength(Double strength) {
        this.strength = strength;
    }

}
